from __future__ import annotations

import re
import os
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


AI_COMMENT_PATTERN = re.compile(r"(?:#|//|--|;+) *(?:ai[!?]*[: -]*) *(.*)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WatchRequest:
    filepath: str
    objective: str
    line_number: int


class FileWatcher:
    def __init__(self, repo_path: str | Path, callback: Callable[[list[WatchRequest]], None]):
        self.repo_path = Path(repo_path).resolve()
        self.callback = callback
        self._stop_event = threading.Event()
        self._thread = None
        self._mtimes: dict[Path, float] = {}
        self._last_requests: set[tuple[str, int, str]] = set() # (path, line, obj) to avoid re-triggering same comment

    def scan_file(self, filepath: Path) -> list[WatchRequest]:
        requests = []
        try:
            if not filepath.is_file():
                return []
                
            # Skip binary or very large files
            if filepath.stat().st_size > 1_000_000:
                return []
                
            lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, line in enumerate(lines, start=1):
                match = AI_COMMENT_PATTERN.search(line)
                if match:
                    objective = match.group(1).strip()
                    # Remove trailing symbols like ! or ? from objective if they were just markers
                    objective = re.sub(r'[!?]+$', '', objective).strip()
                    if not objective:
                        objective = "Fix or improve this code."
                    
                    rel_path = str(filepath.relative_to(self.repo_path))
                    requests.append(WatchRequest(rel_path, objective, i))
        except Exception:
            pass
        return requests

    def _poll(self):
        while not self._stop_event.is_set():
            new_requests = []
            
            # Walk the repo
            for root, dirs, files in os.walk(self.repo_path):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    if file.startswith('.'): continue
                    
                    path = Path(root) / file
                    try:
                        mtime = path.stat().st_mtime
                        if self._mtimes.get(path) != mtime:
                            self._mtimes[path] = mtime
                            
                            # File changed, scan it
                            file_requests = self.scan_file(path)
                            for req in file_requests:
                                key = (req.filepath, req.line_number, req.objective)
                                if key not in self._last_requests:
                                    new_requests.append(req)
                                    self._last_requests.add(key)
                    except (FileNotFoundError, PermissionError):
                        continue
            
            if new_requests:
                self.callback(new_requests)
            
            # Wait before next poll
            time.sleep(2.0)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
