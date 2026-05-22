"""Plan loop — extracted from mission_control_server.py.

Contains all code-generation/plan-loop helpers and API functions.
Pure helpers (no monolith deps) are directly testable.
Functions that call monolith utilities (_chat_base_url, _get_lm_client, etc.)
use lazy imports inside the function body to break the circular import.
"""
from __future__ import annotations

import ast
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from nemo_coding_platform.core.nemo_adapter import NemoCall, NemoCallResult
from nemo_coding_platform.core.nemo_learning import build_project_context, ingest_task_outcome
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.nemo_patterns import (
    classify_exec_error,
    nemo_after_failure,
    nemo_after_success,
    nemo_before_attempt,
)
from nemo_coding_platform.core.reflexion import ReflexionEntry, persist_reflexion
from nemo_coding_platform.core.repo_map import build_repo_map, read_anchor_docs
from nemo_coding_platform.core.sdd import SDDPhase
from nemo_coding_platform.lm_client import LmClient

if TYPE_CHECKING:
    from nemo_coding_platform.mission_control_server import MissionControlServerConfig

# ── Module-level state ────────────────────────────────────────────────────────

_plan_jobs: dict[str, dict[str, object]] = {}
_plan_jobs_lock = threading.Lock()

# ── Score extraction ──────────────────────────────────────────────────────────


def _extract_json_score(critique: str) -> float:
    """Parse a numeric score from the LLM's JSON critique response."""
    json_match = re.search(r'\{.*\}', critique, re.DOTALL)
    if json_match:
        candidate = json_match.group(0)
        for end in range(len(candidate), 0, -1):
            try:
                data = json.loads(candidate[:end])
                s = data.get("score")
                if s is not None:
                    return max(1.0, min(10.0, float(s)))
                break
            except (json.JSONDecodeError, ValueError):
                continue
    m = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', critique)
    if m:
        return max(1.0, min(10.0, float(m.group(1))))
    return 5.0


# ── LM call wrapper ───────────────────────────────────────────────────────────


def _plan_lm_call(
    payload: dict[str, Any],
    system: str,
    user: str,
    max_tokens: int = 1024,
    timeout: int = 120,
    temperature: float = 0.6,
) -> str:
    """Minimal LM call for plan loop — works with LM Studio, NVIDIA NIM, or any OpenAI-compatible endpoint."""
    from nemo_coding_platform.mission_control_server import (  # lazy — avoids circular import
        _chat_base_url,
        _chat_model,
        _get_lm_client,
        _resolve_lmstudio_model,
    )
    base_url = _chat_base_url(payload)
    model = _resolve_lmstudio_model(base_url) or _chat_model(payload)
    is_local = "127.0.0.1:1234" in base_url or "localhost:1234" in base_url
    client = _get_lm_client(payload)
    return client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=float(timeout),
        acquire_timeout=60.0,
        extra_body={"model": model} if model else {},
        with_cooldown=is_local,
    )


# ── Code extraction helpers ───────────────────────────────────────────────────

_SHELL_BARE_LINES: frozenset[str] = frozenset({"fi", "then", "done", "esac", ";;", "do"})

_PY_LINE_START = re.compile(
    r"^(import |from |def |class |for |if |while |with |try:|return |yield |raise |"
    r"print\(|#|@|matplotlib|plt\.|np\.|[A-Za-z_]\w*\s*[=(])"
)


def _strip_shell_artifacts(code: str) -> str:
    """Remove bare shell-keyword lines that would cause NameError in Python."""
    return "\n".join(
        line for line in code.splitlines() if line.strip() not in _SHELL_BARE_LINES
    )


def _extract_ast_valid(text: str) -> str:
    """Find the longest prefix (from the first Python-looking line) that ast.parse() accepts."""
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if _PY_LINE_START.match(line.strip()):
            start = i
            break
    for end in range(len(lines), start, -1):
        candidate = "\n".join(lines[start:end])
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            pass
    return "\n".join(lines[start:])


def _extract_think_snippet(text: str, max_chars: int = 500) -> str:
    """Extract first reasoning block from a thinking-model response."""
    for pattern in (r"<think>(.*?)</think>", r"<\|thinking\|>(.*?)<\|/thinking\|>"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()[:max_chars]
    return ""


def _extract_code_block(text: str, lang: str = "python") -> str:
    """Adaptive cascade extractor — model-agnostic, works regardless of which LLM is loaded.

    Pipeline:
      1. Strip reasoning blocks (<think>, <|thinking|>)
      2. HTML fast path: if content starts with <!doctype or <html, return directly
      2.5 HTML search: for HTML lang, find any <!DOCTYPE html>…</html> block anywhere in text
      3. Prefer explicitly-tagged ```python fence
      4. Accept any fenced block (```bash, ```sh, untagged, …)
      5. Fallback: longest ast-valid substring starting at first Python-looking line
      Shell artifacts (fi, then, done, esac, ;;, do as bare lines) are stripped at every stage.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|thinking\|>.*?<\|/thinking\|>", "", text, flags=re.DOTALL)
    text = text.strip()

    lower_start = text[:40].lower()
    if lower_start.startswith("<!doctype") or lower_start.startswith("<html"):
        return text

    if lang == "html":
        low = text.lower()
        for marker in ("<!doctype html", "<html"):
            idx = low.find(marker)
            if idx >= 0:
                end_idx = low.rfind("</html>", idx)
                if end_idx > idx:
                    return text[idx: end_idx + len("</html>")]

    m = re.search(rf"```{re.escape(lang)}\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return _strip_shell_artifacts(m.group(1).strip())

    m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if m:
        return _strip_shell_artifacts(m.group(1).strip())

    if lang == "html":
        m = re.search(r"```html?\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()

    m = re.search(r"```\w*\n(.*?)```", text, re.DOTALL)
    if m:
        return _strip_shell_artifacts(m.group(1).strip())

    return _strip_shell_artifacts(_extract_ast_valid(text))


# ── Language detection ────────────────────────────────────────────────────────

_LANG_DETECT: dict[str, re.Pattern[str]] = {
    "html": re.compile(
        r"\b(html\s+game|html\s+page|html\s+app|html\s+dashboard|html\s+canvas|"
        r"web\s+game|canvas\s+game|browser\s+game|html5\s+game|interactive\s+html|"
        r"html\s+tool|html\s+widget|html\s+animation|html\s+simulation|"
        r"p[aá]gina\s+html|documento\s+html|archivo\s+html|html\s+completa?o?|"
        r"html\s+animad|html\s+interactiv|html\s+colorid|html\s+autocontenid|"
        r"genera.*html|crea.*html|html.*cuento|html.*historia|html.*juego)\b",
        re.I,
    ),
    "javascript": re.compile(
        r"\b(javascript|node\.?js?|typescript|ts\b|react|vue|express|npm|deno|bun)\b", re.I
    ),
    "bash": re.compile(
        r"\b(bash|shell script|sh script|powershell|zsh|fish|cmd|batch script)\b", re.I
    ),
    "sql": re.compile(
        r"\b(sql|sqlite|mysql|postgres|postgresql|select .* from|create table)\b", re.I
    ),
    "rust": re.compile(r"\b(rust|cargo|rustc|crate)\b", re.I),
    "go": re.compile(r"\b(golang?|go lang)\b", re.I),
}


def _detect_language(objective: str) -> str:
    """Return the target language for code generation (defaults to 'python')."""
    for lang, pat in _LANG_DETECT.items():
        if pat.search(objective):
            return lang
    return "python"


# ── Workspace helpers ─────────────────────────────────────────────────────────

_FILE_MARKER_RE = re.compile(r"^##\s*FILE:\s*(.+?)\s*$", re.M)


def _write_workspace(code: str, workspace: Path) -> tuple[Path, list[Path]]:
    """Parse ## FILE: markers and write files to workspace. Returns (entry_file, all_files)."""
    markers = list(_FILE_MARKER_RE.finditer(code))
    if not markers:
        has_main = re.search(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", code, re.M)
        only_tests = bool(re.search(r"^\s*def test_", code, re.M)) and not has_main
        fname = "test_generated.py" if only_tests else "main.py"
        entry = workspace / fname
        entry.write_text(code, encoding="utf-8")
        return entry, [entry]

    files: list[Path] = []
    for idx, m in enumerate(markers):
        filename = m.group(1).strip()
        start = m.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(code)
        content = code[start:end].strip()
        file_path = workspace / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        files.append(file_path)

    entry = next((f for f in files if not f.name.startswith("test_")), files[0])
    return entry, files


def _try_run_code(
    code: str,
    language: str = "python",
    timeout: int = 30,
    workspace: Path | None = None,
) -> tuple[bool, str]:
    """Attempt to execute code in a subprocess. Returns (success, output)."""
    try:
        if language == "python":
            if workspace is not None:
                entry, _ = _write_workspace(code, workspace)
                cmd = [sys.executable, str(entry)]
                cwd = str(workspace)
            else:
                cmd = [sys.executable, "-c", code]
                cwd = None
        elif language == "javascript":
            cmd = ["node", "-e", code]
            cwd = None
        elif language == "bash":
            cmd = ["bash", "-c", code]
            cwd = None
        elif language == "sql":
            sql_runner = (
                "import sqlite3, sys\n"
                "conn = sqlite3.connect(':memory:')\n"
                f"sql = {repr(code)}\n"
                "try:\n"
                "    for stmt in sql.split(';'):\n"
                "        s = stmt.strip()\n"
                "        if s:\n"
                "            cur = conn.execute(s)\n"
                "            rows = cur.fetchall()\n"
                "            if rows: print('\\n'.join(str(r) for r in rows))\n"
                "except Exception as e: print(f'Error: {e}', file=sys.stderr); sys.exit(1)\n"
            )
            cmd = [sys.executable, "-c", sql_runner]
            cwd = None
        elif language == "html":
            trimmed = code.strip()
            if not trimmed or ("<html" not in trimmed.lower() and "<!doctype" not in trimmed.lower()):
                return False, "HTML must contain <html> or <!doctype html>"
            if workspace is not None:
                out_file = workspace / "output.html"
                out_file.write_text(code, encoding="utf-8")
            return True, f"HTML artifact generated ({len(trimmed)} chars)"
        else:
            return True, f"execution skipped (language: {language})"
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd if language == "python" and workspace else None,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode == 0, out[:800]
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return True, f"runtime not found ({exc.filename}) — execution skipped"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:400]


# ── Inline code execution API ─────────────────────────────────────────────────


def api_code_exec(config: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a code artifact inline. Returns stdout, stderr, exit_code, duration_ms, and base64 images."""
    code = str(payload.get("code", "")).strip()
    lang_raw = str(payload.get("language", "python")).lower().strip()
    timeout_sec = min(int(payload.get("timeout", 30)), 60)

    if not code:
        return {"error": "code is required", "exec_ok": False, "stdout": "", "stderr": "", "exit_code": -1, "duration_ms": 0, "images": []}

    _lang_aliases = {"py": "python", "js": "javascript", "node": "javascript", "sh": "bash"}
    lang = _lang_aliases.get(lang_raw, lang_raw)
    if lang not in ("python", "javascript", "bash", "sql"):
        return {"error": f"unsupported language: {lang_raw}", "exec_ok": False, "stdout": "", "stderr": "", "exit_code": -1, "duration_ms": 0, "images": []}

    if lang == "python" and "matplotlib" in code and "matplotlib.use(" not in code:
        code = "import matplotlib\nmatplotlib.use('Agg')\n" + code

    workspace = Path(tempfile.mkdtemp(prefix="repl_"))
    start_ts = time.time()
    try:
        if lang == "python":
            entry, _ = _write_workspace(code, workspace)
            cmd = [sys.executable, str(entry)]
            cwd = str(workspace)
        elif lang == "javascript":
            cmd = ["node", "-e", code]
            cwd = None
        elif lang == "bash":
            cmd = ["bash", "-c", code]
            cwd = None
        else:  # sql
            sql_runner = (
                "import sqlite3, sys\nconn = sqlite3.connect(':memory:')\n"
                f"sql = {repr(code)}\n"
                "try:\n"
                "  for s in sql.split(';'):\n"
                "    s = s.strip()\n"
                "    if s:\n"
                "      cur = conn.execute(s)\n"
                "      rows = cur.fetchall()\n"
                "      if rows: print('\\n'.join(str(r) for r in rows))\n"
                "except Exception as e: print(f'Error: {e}', file=sys.stderr); sys.exit(1)\n"
            )
            cmd = [sys.executable, "-c", sql_runner]
            cwd = None

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, cwd=cwd)
        duration_ms = int((time.time() - start_ts) * 1000)

        images: list[dict[str, str]] = []
        if lang == "python":
            for img_path in sorted(workspace.glob("*.png")) + sorted(workspace.glob("*.jpg")) + sorted(workspace.glob("*.svg")):
                try:
                    data = img_path.read_bytes()
                    mime = "image/svg+xml" if img_path.suffix == ".svg" else ("image/jpeg" if img_path.suffix in (".jpg", ".jpeg") else "image/png")
                    images.append({"name": img_path.name, "data_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
                except Exception:  # noqa: BLE001
                    pass

        return {
            "exec_ok": result.returncode == 0,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:2000],
            "exit_code": result.returncode,
            "duration_ms": duration_ms,
            "images": images,
        }
    except subprocess.TimeoutExpired:
        return {"exec_ok": False, "stdout": "", "stderr": f"Timeout after {timeout_sec}s", "exit_code": -1, "duration_ms": int((time.time() - start_ts) * 1000), "images": []}
    except Exception as exc:  # noqa: BLE001
        return {"exec_ok": False, "stdout": "", "stderr": str(exc)[:800], "exit_code": -1, "duration_ms": 0, "images": []}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ── Parallel candidate generation ─────────────────────────────────────────────

_CANDIDATE_TEMPS = [0.4, 0.7, 1.0]


def _generate_candidates(payload: dict[str, Any], system: str, user: str, n: int = 3) -> list[str]:
    """Run n parallel LM generation calls with different temperatures; return all responses."""
    temps = _CANDIDATE_TEMPS[:n]
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {
            ex.submit(_plan_lm_call, payload, system, user, 2048, 300, t): t
            for t in temps
        }
        results: list[str] = []
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass
    return results


# ── Visual critique ───────────────────────────────────────────────────────────


def _visual_critique_lm_call(payload: dict[str, Any], objective: str, image_path: str = "hand.png") -> str | None:
    """Send the generated image to a multimodal LM for visual scoring. Returns raw JSON string or None."""
    from nemo_coding_platform.mission_control_server import (  # lazy — avoids circular import
        _chat_base_url,
        _get_lm_client,
        _resolve_lmstudio_model,
    )
    try:
        img_bytes = Path(image_path).read_bytes()
    except (FileNotFoundError, OSError):
        return None
    b64 = base64.b64encode(img_bytes).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual art critic. Reply ONLY with JSON — no prose. "
                'Format: {"score":<int 1-10>,"present":[<str>],"missing":[<str>],"summary":"<str>"}'
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Rate this image for task: {objective[:120]}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        },
    ]
    vis_base_url = _chat_base_url(payload)
    model = _resolve_lmstudio_model(vis_base_url) or ""
    try:
        return _get_lm_client(payload).chat(
            messages,
            temperature=0.0,
            max_tokens=200,
            timeout=60.0,
            acquire_timeout=60.0,
            extra_body={"model": model} if model else {},
        )
    except Exception:  # noqa: BLE001
        return None


# ── NEMO adapter ──────────────────────────────────────────────────────────────


class _PlanNemoAdapter:
    """Adapter that bridges the plan loop's _nemo() callable to the nemo_patterns interface.

    nemo_patterns functions expect adapter.call(phase, tool_name, **kw) → (adapter, NemoCallResult).
    The plan loop's _nemo() expects (tool_name, phase_str, **kw) → dict.
    """

    def __init__(self, nemo_fn: Any) -> None:
        self._fn = nemo_fn

    def call(self, phase: Any, tool_name: str, **kw: Any) -> tuple["_PlanNemoAdapter", NemoCallResult]:
        phase_str = str(getattr(phase, "value", phase))
        try:
            raw = self._fn(tool_name, phase_str, **kw)
            payload = raw if isinstance(raw, dict) else {}
            ok = not payload.get("error")
            call = NemoCall(phase=phase, tool_name=tool_name, arguments=kw)
            return self, NemoCallResult(call=call, ok=ok, payload=payload)
        except Exception as exc:  # noqa: BLE001
            call = NemoCall(phase=phase, tool_name=tool_name, arguments=kw)
            return self, NemoCallResult(call=call, ok=False, payload={"error": str(exc)})


# ── Pytest harness ────────────────────────────────────────────────────────────


def _run_pytest_harness(code: str, tmpdir: "Path", timeout: int = 30) -> dict[str, Any]:
    """Run pytest on generated code if it contains test functions.

    Returns dict with keys: skipped (bool), passed, failed, errors (int), output (str).
    Never raises — errors are captured in the returned dict.
    """
    try:
        if not re.search(r"^\s*def test_", code, re.M):
            return {"skipped": True, "passed": 0, "failed": 0, "errors": 0, "output": ""}
        test_file = tmpdir / "test_generated.py"
        if not test_file.exists():
            test_file.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "--no-header", "-q"],
            capture_output=True, text=True, timeout=timeout, cwd=str(tmpdir),
        )
        out = (result.stdout + result.stderr).strip()
        passed = len(re.findall(r" PASSED", out))
        failed = len(re.findall(r" FAILED", out))
        errors = len(re.findall(r" ERROR", out))
        return {"skipped": False, "passed": passed, "failed": failed, "errors": errors, "output": out[:800]}
    except subprocess.TimeoutExpired:
        return {"skipped": False, "passed": 0, "failed": 0, "errors": 1, "output": f"pytest timeout after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"skipped": False, "passed": 0, "failed": 0, "errors": 1, "output": str(exc)[:400]}


# ── Adaptive temperature & critique system prompt ─────────────────────────────


def _adaptive_temp(best_score: float) -> float:
    """Return generation temperature based on current best quality score."""
    if best_score >= 9.0:
        return 0.2
    if best_score >= 6.0:
        return 0.5
    return 0.8


def _build_critique_sys(best_score: float) -> str:
    """Return a mode-specific critique system prompt based on current best score."""
    base = (
        "You are a code quality evaluator for automation scripts. "
        "Reply ONLY with a JSON object — no markdown, no prose. "
        'Format: {"score":<int 1-10>,"present":[<str>],"missing":[<str>],'
        '"improvements":[<str>],"summary":"<str>"}\n'
        "Scoring guide:\n"
        "  10 = perfect: task fully accomplished, output is excellent, code is clean and complete "
        "(for charts: has title, labeled axes, tight layout, good styling — nothing missing).\n"
        "  9 = very good: task accomplished and runs correctly, minor polish missing "
        "(e.g. missing axis label, no tight_layout, could have better colors).\n"
        "  7-8 = good: works but has gaps — missing features, suboptimal output, or minor errors.\n"
        "  5-6 = partial: runs but output is incomplete or partially wrong.\n"
        "  3-4 = broken: mostly fails or misses the objective.\n"
        "  1-2 = completely wrong: does not run or produces nothing useful.\n"
        "Execution success (exit code 0) is the primary signal — do not score below 7 if it runs without errors."
    )
    if best_score >= 9.0:
        return (
            base + " PERFECTION MODE: push for 10/10. List every specific micro-detail missing: "
            "title, axis labels, units, legend, grid, color, DPI, tight_layout, edge cases. "
            "Be concrete — vague praise earns 9, specific completeness earns 10."
        )
    if best_score >= 6.0:
        return (
            base + " IMPROVEMENT MODE: the code works. Identify concrete improvements that raise quality: "
            "missing visual polish, output gaps, robustness issues. Be specific."
        )
    return (
        base + " CORRECTION MODE: focus on whether the code accomplishes the stated task. "
        "If execution succeeded, identify exactly what specific output or behavior is still missing."
    )


# ── Plan loop generator ───────────────────────────────────────────────────────


def api_agent_plan_gen(  # type: ignore[return]
    config: "MissionControlServerConfig",
    payload: dict[str, object],
    job_id: str = "",
):
    """Generator version of the autonomous plan loop. Yields event dicts per iteration then a final 'done' event."""
    from nemo_coding_platform.mission_control_server import (  # lazy — avoids circular import
        POLLINATIONS_TOOLS,
        _bad_request,
        _chat_base_url,
        _execute_pollinations_tool,
        _load_settings,
        _nemo_chat_tool_call,
        _parse_llm_tool_calls,
        _require_nemo_mcp_url,
    )

    settings = _load_settings(config)
    payload = {**settings, **payload}

    objective = str(payload.get("objective") or "").strip()
    if not objective:
        raise _bad_request("objective is required for plan mode", error_code="missing_objective")

    max_iterations = max(1, min(10, int(payload.get("max_iterations") or 5)))
    quality_threshold = max(0.1, float(payload.get("quality_threshold") or 7.5))
    topic = str(payload.get("topic") or "autonomous_plan").strip()
    use_parallel = bool(payload.get("parallel_candidates", True))
    use_visual = bool(payload.get("visual_critique", True))
    isolate_context = bool(payload.get("isolate_context", False))
    obj_hash = "obj_" + md5(objective.lower().strip().encode()).hexdigest()[:10]
    nemo_mcp_url = _require_nemo_mcp_url(payload)
    tool_calls: list[dict[str, object]] = []

    if job_id:
        with _plan_jobs_lock:
            _plan_jobs[job_id] = {"cancel": False, "steer": None}

    def _nemo(tool_name: str, phase: str, **kw: Any) -> dict[str, Any]:
        return _nemo_chat_tool_call(
            config, tool_calls, tool_name,
            lifecycle_phase=phase, nemo_mcp_url=nemo_mcp_url, **kw,
        )

    if not isolate_context:
        _nemo("context_bootstrap", "start", task=objective, topic=topic, token_budget=600, limit=6)

    _nemo_adapter = _PlanNemoAdapter(_nemo)

    iterations: list[dict[str, Any]] = []
    best_code = ""
    best_score = 0.0
    best_exec_ok = False
    final_score = 0.0
    think_snippet = ""
    _plan_tmpdir: Path | None = None
    last_exec_output = ""
    harness_total_failed = 0

    consecutive_perfect = 0
    plateau_count = 0
    prev_score = 0.0
    stop_reason = "max_iterations"

    try:
        _base_url_str = _chat_base_url(payload)
    except Exception:
        _base_url_str = "http://localhost:1234/v1"
    _is_local_llm = "localhost" in _base_url_str or "127.0.0.1" in _base_url_str

    _VIZ_KWS = re.compile(
        r"\b(plot|chart|graph|visuali[sz]|draw|diagr|spiral|matplotlib|pyplot|figure|bar|pie|"
        r"scatter|histogram|heatmap|contour|3d|surface|render|image|pixel)\b",
        re.I,
    )
    is_viz = bool(_VIZ_KWS.search(objective))
    lang = _detect_language(objective)

    _TEST_KWS = re.compile(
        r"\b(pytest|unit.?test|test.?function|test.?case|def test_|tdd|test.?suite|"
        r"write.{0,15}test|add.{0,15}test|generat.{0,15}test|assert.{0,15}correct)\b",
        re.I,
    )
    is_test = lang == "python" and bool(_TEST_KWS.search(objective))

    _COMPLEX_KWS = re.compile(
        r"\b(module|package|project|multiple.?file|multi.?file|class.*and.*class|"
        r"cli.?(tool|app)|library|framework|implement.{0,20}with.{0,20}(util|helper|model|service)|"
        r"split.{0,15}file|separate.{0,15}file|complex|full.?stack|web.?app|api.?server|"
        r"design.?pattern|architecture)\b",
        re.I,
    )
    is_complex = lang == "python" and bool(_COMPLEX_KWS.search(objective)) and not is_viz

    if is_viz:
        gen_sys = (
            "You are an expert code generator. Output ONLY raw code — no markdown fences, no explanations, no comments. "
            "Use whatever language, library, or approach best accomplishes the objective. "
            "SUBPROCESS CONSTRAINTS (code runs headless, no display attached): "
            "(1) For any visualization library that needs a display backend (matplotlib, seaborn, plotly static, PIL, etc.), "
            "configure headless mode before other imports — e.g. 'import matplotlib; matplotlib.use(\"Agg\")' for matplotlib. "
            "(2) Save output to the EXACT filename(s) the objective specifies. "
            "(3) Never call plt.show() or open any interactive/blocking GUI. "
            "(4) Code must be complete and self-contained — no external data files, no placeholders, no user input()."
        )
    elif is_complex:
        gen_sys = (
            "You are a Python multi-file project generator. "
            "Output ONLY raw Python code structured as multiple files using this exact format:\n"
            "## FILE: filename.py\n"
            "<file contents>\n\n"
            "## FILE: another.py\n"
            "<file contents>\n\n"
            "Rules: "
            "(1) First file listed is the entry point (main.py or similar). "
            "(2) Use relative imports between files — they run from the same directory. "
            "(3) Every file is complete and syntactically valid Python. "
            "(4) No markdown fences, no explanations outside FILE markers. "
            "(5) Do NOT use input(), GUI libraries, or blocking calls. "
            "(6) The entry point must print meaningful output to stdout."
        )
    elif is_test:
        gen_sys = (
            "You are a Python code generator that writes implementation code AND pytest tests in a single file. "
            "Output ONLY raw Python code — no markdown fences, no explanations, no comments. "
            "Keep it under 100 lines. "
            "CRITICAL rules: "
            "(1) Write the implementation functions first (no 'test_' prefix). "
            "(2) Then write pytest test functions — each MUST start with 'def test_' exactly. "
            "    Example: 'def test_add_positive(): assert add(1,2) == 3' "
            "(3) Do NOT use unittest.TestCase — use plain pytest 'def test_*' functions only. "
            "(4) Do NOT use input(), GUI libraries, or any blocking calls. "
            "(5) Do NOT wrap tests in 'if __name__ == \"__main__\"'. "
            "(6) All imports (including pytest if used) at the top of the file."
        )
    elif lang == "html":
        gen_sys = (
            "You are an expert HTML/CSS/JavaScript developer. "
            "Output ONLY a complete self-contained HTML document — no Python, no matplotlib, no markdown fences, no explanations, no prose. "
            "Your ENTIRE response must start with <!doctype html> or <html> — NOTHING before it. "
            "STRICTLY FORBIDDEN: import statements, Python code, plt., fig., ax., matplotlib, pyplot. "
            "RULES: "
            "(1) Single HTML file with all CSS inside <style> and all JS inside <script> tags. "
            "(2) No localStorage or sessionStorage — use plain JS variables instead. "
            "(3) No external URLs, CDN scripts, or network requests — everything must be inline. "
            "(4) No alert(), confirm(), or prompt() calls. "
            "(5) For games: use requestAnimationFrame for game loops and <canvas> for rendering. "
            "(6) The document must be fully functional with no missing pieces."
        )
    elif lang == "python":
        gen_sys = (
            "You are a Python code generator. Output ONLY raw Python code — no markdown fences, "
            "no explanations, no comments. Keep it under 80 lines. "
            "IMPORTANT rules: "
            "(1) The code must be self-contained and runnable with no missing imports. "
            "(2) Do NOT use input(), GUI libraries (tkinter, pygame, wx, SDL), or any blocking calls — this runs HEADLESS with no display. "
            "(3) If the objective requires a package not in stdlib, add 'import subprocess, sys; subprocess.check_call([sys.executable, \"-m\", \"pip\", \"install\", \"<pkg>\"], stdout=subprocess.DEVNULL)' at the top. "
            "(4) Print meaningful output to stdout so results are visible. "
            "(5) Handle all edge cases — the code will be executed and the output verified."
        )
    else:
        lang_display = lang.capitalize()
        gen_sys = (
            f"You are a {lang_display} code generator. "
            f"Output ONLY raw {lang_display} code — no markdown fences, no explanations, no comments. "
            "Keep it concise and complete. "
            "IMPORTANT rules: "
            "(1) The code must be self-contained and correct. "
            "(2) Do NOT add placeholders or TODOs — produce fully working code. "
            "(3) Include all necessary imports or dependencies at the top. "
            "(4) If the code produces output, print it to stdout."
        )

    _sdd_phase = SDDPhase.SPEC if is_test else SDDPhase.IMPLEMENT

    _plan_repo = str(getattr(config, "repo_path", "") or "")
    if _plan_repo:
        try:
            _repo_map = build_repo_map(
                _plan_repo,
                cache_path=Path(_plan_repo) / ".spacecode-runtimes" / "mission-control" / ".repo_map_cache.json",
                max_chars=2000,
            )
            if _repo_map:
                gen_sys = (
                    f"## Project file structure\n{_repo_map}\n\n"
                    "Use the above to understand which modules already exist and what imports are available.\n\n"
                ) + gen_sys
        except Exception:
            pass
        try:
            _anchor_docs = read_anchor_docs(_plan_repo, max_chars_each=600)
            if _anchor_docs:
                gen_sys = f"## Project documentation\n{_anchor_docs}\n\n" + gen_sys
        except Exception:
            pass
        if _nemo_adapter:
            try:
                _learning_ctx = build_project_context(_nemo_adapter, repo_path=_plan_repo, task=objective)
                if _learning_ctx:
                    gen_sys = _learning_ctx + "\n\n" + gen_sys
            except Exception:
                pass

    _plan_start_time = time.time()
    _preexisting_images: set[str] = set()
    for _ext in ("png", "jpg", "jpeg", "svg"):
        for _f in Path(".").glob(f"*.{_ext}"):
            _preexisting_images.add(_f.name)

    yield {"type": "start", "objective": objective, "max_iterations": max_iterations, "quality_threshold": quality_threshold, "job_id": job_id}

    for i in range(1, max_iterations + 1):
        if job_id:
            with _plan_jobs_lock:
                _ctrl = _plan_jobs.get(job_id, {})
                should_cancel = _ctrl.get("cancel")
                _steer_directive = str(_ctrl.get("steer") or "")
                if _steer_directive:
                    _ctrl["steer"] = None
            if should_cancel:
                yield {"type": "cancelled", "job_id": job_id, "iterations_run": len(iterations),
                       "best_score": best_score}
                with _plan_jobs_lock:
                    _plan_jobs.pop(job_id, None)
                return
        else:
            _steer_directive = ""

        _prev_error_class = classify_exec_error(last_exec_output, last_exec_output == "")
        nemo_hint = nemo_before_attempt(
            _nemo_adapter,
            query=f"{lang} {_prev_error_class} fix best practices",
            tags=("code_pattern", lang),
            limit=3,
        )
        nemo_hint += nemo_before_attempt(
            _nemo_adapter,
            query=f"{topic} {lang} {_prev_error_class} error fix",
            tags=("plan_loop", lang, obj_hash),
            limit=3,
        )

        if i > 1 and iterations:
            _prev = iterations[-1]
            _lesson = (
                f"[intra-job {job_id[:12] if job_id else 'nojob'}] iter {i - 1}/{max_iterations}: "
                f"score {_prev['score']:.1f}/10 exec={'OK' if _prev['exec_ok'] else 'FAIL'}. "
                f"Insight: {_prev['critique_text'][:200]}"
            )
            _nemo(
                "cognitive_ingest", "review",
                content=_lesson,
                memory_type="episodic",
                tags=("intra_job", lang, topic, obj_hash),
                context=f"Plan loop intra-job reflexion after iteration {i - 1}",
            )

        if i == 1:
            if is_viz:
                extra = (
                    "Save all output to the EXACT filename(s) the task specifies. "
                    "Code must run headless and be fully self-contained. "
                    "For charts: include a descriptive title, labeled axes, tight_layout(), and save at DPI>=120. "
                    "Under 80 lines."
                )
                lang_tag = "Python"
            elif is_complex:
                extra = (
                    "REQUIRED: use '## FILE: filename.py' markers to split into multiple files. "
                    "First file is the entry point. Each file must be complete and importable. "
                    "No markdown fences — FILE markers only."
                )
                lang_tag = "Python"
            elif is_test:
                extra = (
                    "REQUIRED: include BOTH implementation functions AND pytest test functions. "
                    "Every test function MUST start with 'def test_' (not inside a class, not inside main). "
                    "Keep total code under 100 lines."
                )
                lang_tag = "Python"
            elif lang == "html":
                extra = (
                    "Output a COMPLETE self-contained HTML document with all CSS in <style> and all JS in <script>. "
                    "Start with <!doctype html>. No external URLs, CDN links, or network requests. "
                    "No alert/confirm/prompt. The page must be fully functional and visually rich. "
                    "FORBIDDEN: Python, import, matplotlib, pyplot, plt., fig., ax. — output ONLY raw HTML."
                )
                lang_tag = "HTML"
            else:
                extra = "Output must run without errors. Print results to stdout. Keep total code under 75 lines."
                lang_tag = lang.capitalize()
            gen_user = (
                f"Task: {objective}\n\n"
                f"Rules: output ONLY valid {lang_tag} code. {extra}{nemo_hint}"
            )
        else:
            last = iterations[-1]
            _refine_mode = last["score"] >= 6.0 and bool(best_code)
            exec_hint = ""
            if not last["exec_ok"] and last["exec_output"]:
                exec_hint = f"\nEXECUTION ERROR (fix this first): {last['exec_output'][:400]}\n"
            steer_hint = f"\nUSER DIRECTIVE (apply this now): {_steer_directive}\n" if _steer_directive else ""
            line_limit = 60 if is_viz else (100 if is_test else 80)
            lang_tag = "Python" if lang == "python" else lang.capitalize()
            if lang == "html":
                lang_tag = "HTML"
            test_reminder = (
                " CRITICAL: keep all 'def test_*' functions — do NOT remove them or rename to non-test_ prefix."
                if is_test else
                " CRITICAL: keep '## FILE:' markers — output must remain multi-file format."
                if is_complex else
                " CRITICAL: keep the complete HTML structure — improve without removing sections."
                if lang == "html" else ""
            )
            if _refine_mode:
                gen_user = (
                    f"Refine this {lang_tag} code (best score: {best_score:.1f}/10, last attempt: {last['score']:.1f}/10)."
                    f"{exec_hint}{steer_hint}\n"
                    f"Critique: {last['critique_text'][:300]}\n\n"
                    f"Current best code to improve:\n{best_code[:2000]}\n\n"
                    f"Return ONLY the improved complete {lang_tag}."
                    f" DO NOT rewrite from scratch — edit and enhance the existing code.{test_reminder}{nemo_hint}"
                )
            else:
                gen_user = (
                    f"Generate new {lang_tag} code from scratch (previous attempt scored {last['score']:.1f}/10 — too low to refine)."
                    f"{exec_hint}{steer_hint}\n"
                    f"Critique of previous attempt: {last['critique_text'][:300]}\n\n"
                    f"Task: {objective}\n\n"
                    f"Rules: output ONLY valid complete {lang_tag} code."
                    f"{' Under ' + str(line_limit) + ' lines.' if lang != 'html' else ''}{test_reminder}{nemo_hint}"
                )

        if _is_local_llm:
            time.sleep(2.0)

        code = ""
        code_response = ""
        lm_error: str = ""
        try:
            _gen_max_tokens = 4096 if lang == "html" else 2048
            _gen_temp = _adaptive_temp(best_score)
            _gen_timeout = 600
            responses = [_plan_lm_call(payload, gen_sys, gen_user, max_tokens=_gen_max_tokens, timeout=_gen_timeout, temperature=_gen_temp)]
        except Exception as _lm_exc:  # noqa: BLE001
            lm_error = str(_lm_exc)[:300]
            if _steer_directive and job_id:
                with _plan_jobs_lock:
                    if job_id in _plan_jobs:
                        _plan_jobs[job_id]["steer"] = _steer_directive
            yield {"type": "error", "error": f"LM call failed at iteration {i}: {lm_error}"}
            break

        think_snippet = _extract_think_snippet(responses[0]) if responses else ""

        def _ast_ok(c: str) -> bool:
            if lang != "python":
                return bool(c.strip())
            if not c.strip():
                return False
            markers = list(_FILE_MARKER_RE.finditer(c))
            if markers:
                blocks = []
                for idx, m in enumerate(markers):
                    start = m.end()
                    end = markers[idx + 1].start() if idx + 1 < len(markers) else len(c)
                    blocks.append(c[start:end].strip())
            else:
                blocks = [c]
            try:
                for block in blocks:
                    if block:
                        ast.parse(block)
                return True
            except SyntaxError:
                return False

        for resp in responses:
            candidate = _extract_code_block(resp, lang)
            if _ast_ok(candidate):
                code = candidate
                code_response = resp
                break
        if not code:
            longest = max(responses, key=len) if responses else ""
            candidate = _extract_code_block(longest, lang)
            syntax_err_msg = ""
            if _ast_ok(candidate):
                code = candidate
                code_response = longest
            else:
                if lang == "python":
                    try:
                        ast.parse(candidate)
                    except SyntaxError as se:
                        syntax_err_msg = f"{se.msg} at line {se.lineno}"
                fix_user = (
                    f"SyntaxError: {syntax_err_msg}\n\nBroken code:\n{candidate}\n\n"
                    f"Return ONLY corrected {lang.capitalize()} code."
                )
                try:
                    fix_resp = _plan_lm_call(payload, gen_sys, fix_user, max_tokens=2048, timeout=120, temperature=0.0)
                    fixed = _extract_code_block(fix_resp, lang)
                    if _ast_ok(fixed):
                        code = fixed
                        code_response = fix_resp
                    else:
                        raise SyntaxError("still invalid after fix")
                except (SyntaxError, Exception):  # noqa: BLE001
                    err_msg = f"SyntaxError: {syntax_err_msg}" if syntax_err_msg else "code extraction failed"
                    record: dict[str, Any] = {
                        "iteration": i, "code": candidate, "critique_text": "{}",
                        "score": 1.0, "exec_ok": False, "exec_output": err_msg,
                    }
                    iterations.append(record)
                    yield {
                        "type": "iteration", "iteration": i, "score": 1.0,
                        "exec_ok": False, "exec_output": err_msg,
                        "critique_summary": "{}", "code_chars": len(candidate),
                    }
                    continue

        if _plan_tmpdir is None:
            _plan_tmpdir = Path(tempfile.mkdtemp(prefix="plan_ws_"))

        exec_ok, exec_output = _try_run_code(code, lang, workspace=_plan_tmpdir if lang in ("python", "html") else None)

        harness_result: dict[str, Any] = {}
        if lang == "python" and exec_ok:
            harness_result = _run_pytest_harness(code, _plan_tmpdir)
            if not harness_result.get("skipped") and harness_result.get("failed", 0) > 0:
                exec_ok = False
                exec_output = harness_result["output"][:600]
            harness_total_failed += harness_result.get("failed", 0)

        visual_raw: str | None = None
        if exec_ok and use_visual:
            _vis_image: str | None = None
            for _vext in ("png", "jpg", "jpeg", "svg"):
                _candidates = (
                    list(_plan_tmpdir.glob(f"*.{_vext}")) if _plan_tmpdir and _plan_tmpdir.exists() else []
                ) + list(Path(".").glob(f"*.{_vext}"))
                if _candidates:
                    _vis_image = str(_candidates[0])
                    break
            if _vis_image:
                visual_raw = _visual_critique_lm_call(payload, objective, image_path=_vis_image)

        exec_note = "Execution: OK" if exec_ok else f"Execution FAILED: {exec_output[:300]}"
        if harness_result and not harness_result.get("skipped"):
            exec_note += f" | Tests: {harness_result.get('passed',0)} passed, {harness_result.get('failed',0)} failed"
        critique_sys = _build_critique_sys(best_score)
        _critique_target = best_code if best_code else code
        _score_ctx = f" (previous best: {best_score:.1f}/10)" if best_score > 0 else ""
        _plan_media_calls = [
            inv for inv in _parse_llm_tool_calls(code_response)
            if str(inv.get("tool")) in POLLINATIONS_TOOLS
        ]
        _plan_artifact_note = ""
        if _plan_media_calls:
            _plan_media_results: dict[str, dict[str, object]] = {}
            with ThreadPoolExecutor(max_workers=len(_plan_media_calls)) as _plan_pool:
                _plan_futs = {
                    _plan_pool.submit(_execute_pollinations_tool, inv, config): inv
                    for inv in _plan_media_calls
                }
                for _plan_fut in as_completed(_plan_futs):
                    _plan_inv = _plan_futs[_plan_fut]
                    _plan_media_results[str(_plan_inv.get("tool"))] = _plan_fut.result()
            _artifact_lines: list[str] = []
            for inv in _plan_media_calls:
                tool_name = str(inv.get("tool"))
                res = _plan_media_results.get(tool_name, {})
                if "artifact_path" in res:
                    _artifact_lines.append(f"- {tool_name}: {res['artifact_path']}")
                elif "text" in res:
                    _artifact_lines.append(f"- {tool_name} result: {str(res['text'])[:200]}")
            if _artifact_lines:
                _plan_artifact_note = "\n\n[Media artifacts generated this iteration]\n" + "\n".join(_artifact_lines)

        critique_user = (
            f"Task: {objective[:200]}\n{exec_note}\n\n"
            f"Code{_score_ctx}:\n{_critique_target[:1500]}"
            f"{_plan_artifact_note}"
        )
        _critique_failed = False
        try:
            critique_raw = _plan_lm_call(payload, critique_sys, critique_user, max_tokens=512, timeout=240, temperature=0.0)
        except Exception:  # noqa: BLE001
            critique_raw = '{"score":5,"present":[],"missing":[],"improvements":[],"summary":"unavailable"}'
            _critique_failed = True

        text_score = _extract_json_score(critique_raw)
        visual_score = _extract_json_score(visual_raw) if visual_raw else 0.0
        score = max(text_score, visual_score) if visual_score > 0 else text_score
        if not exec_ok:
            score = min(score, 5.0)
        if harness_result and not harness_result.get("skipped") and harness_result.get("passed", 0) > 0 and harness_result.get("failed", 0) == 0:
            score = min(10.0, score + 0.5)
        _has_test_failures = harness_result is not None and harness_result.get("failed", 0) > 0
        if exec_ok and not _has_test_failures and not _critique_failed and 4.0 <= score < 7.0:
            score = 7.0

        if not exec_ok:
            last_exec_output = exec_output
        if score > best_score:
            best_score, best_code, best_exec_ok = score, code, exec_ok

        if _is_local_llm:
            time.sleep(2.0)

        critique_summary = critique_raw[:300]
        _cur_error_class = classify_exec_error(exec_output, exec_ok)
        if not exec_ok or score < 5.0:
            nemo_after_failure(
                _nemo_adapter,
                evidence=(
                    f"objective={objective[:120]} lang={lang} score={score:.1f}/10\n"
                    f"error_class={_cur_error_class} exec_error={exec_output[:200]}\n"
                    f"critique={critique_summary}"
                ),
                task_id=topic,
                attempt_n=i,
                tags=("plan_loop", "plan_failure", lang, topic, obj_hash, _cur_error_class),
            )
        elif score >= quality_threshold:
            nemo_after_success(
                _nemo_adapter,
                solution_summary=(
                    f"objective={objective[:120]} lang={lang} score={score:.1f}/10\n"
                    f"critique={critique_summary}\ncode_preview={code[:400]}"
                ),
                task_id=topic,
                tags=("plan_loop", "plan_success", lang, topic, obj_hash),
            )
            if score >= 9.0 and exec_ok and critique_summary:
                _nemo(
                    "cognitive_ingest", "review",
                    content=(
                        f"[code_pattern][{lang}] High-quality pattern (score {score:.0f}/10, exec ok):\n"
                        f"{critique_summary[:400]}"
                    ),
                    memory_type="code_pattern",
                    tags=("code_pattern", lang, topic),
                    context="Extracted from successful plan loop run",
                )
        else:
            _nemo(
                "cognitive_ingest", "review",
                content=(
                    f"Plan loop iter {i}/{max_iterations} — {objective[:100]}\n"
                    f"lang={lang} score={score:.1f}/10 exec={'ok' if exec_ok else 'fail'}\n"
                    f"critique={critique_summary}"
                ),
                memory_type="evidence",
                tags=("plan", "autonomous", "iteration", topic, obj_hash),
                context=f"Autonomous plan loop iteration {i}",
            )

        if _sdd_phase == SDDPhase.SPEC and exec_ok:
            _sdd_phase = SDDPhase.IMPLEMENT
        elif _sdd_phase == SDDPhase.IMPLEMENT and exec_ok and score >= quality_threshold:
            _sdd_phase = SDDPhase.VALIDATE

        record = {
            "iteration": i,
            "code": code,
            "critique_text": critique_raw,
            "score": score,
            "exec_ok": exec_ok,
            "exec_output": exec_output,
            "harness": harness_result,
            "sdd_phase": str(_sdd_phase),
        }
        iterations.append(record)
        final_score = score

        yield {
            "type": "iteration",
            "iteration": i,
            "score": score,
            "exec_ok": exec_ok,
            "exec_output": exec_output,
            "critique_summary": critique_raw[:300],
            "visual_critique": visual_raw[:200] if visual_raw else None,
            "code_chars": len(code),
            "code": code,
            "lang": lang,
            "think_snippet": think_snippet[:400],
            "harness": harness_result if not harness_result.get("skipped") else None,
            "sdd_phase": str(_sdd_phase),
        }

        _delta = score - prev_score
        if score >= 9.5:
            consecutive_perfect += 1
        else:
            consecutive_perfect = 0

        if i > 1 and _delta < 0.5 and best_score >= 6.0:
            plateau_count += 1
        else:
            plateau_count = 0

        prev_score = score

        if consecutive_perfect >= 2:
            stop_reason = "consecutive_perfect"
            break
        if i > 1 and score < best_score - 2.0:
            stop_reason = "regression"
            break
        if plateau_count >= 2:
            stop_reason = "plateau"
            break
        if score >= quality_threshold:
            stop_reason = "quality_threshold"
            break

    # --- Copy generated image artifacts to artifacts folder ---
    artifact_file: str | None = None
    artifact_html_content: str | None = None
    try:
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "images" / "plans"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        search_dirs = []
        if _plan_tmpdir is not None and _plan_tmpdir.exists():
            search_dirs.append(_plan_tmpdir)
        search_dirs.append(Path("."))
        for search_dir in search_dirs:
            for ext in ("png", "jpg", "jpeg", "svg"):
                for img in search_dir.glob(f"*.{ext}"):
                    is_tmpdir = search_dir == _plan_tmpdir
                    is_new_in_cwd = img.name not in _preexisting_images and img.stat().st_mtime >= _plan_start_time
                    if not is_tmpdir and not is_new_in_cwd:
                        continue
                    dest = artifacts_dir / img.name
                    shutil.copy2(img, dest)
                    if artifact_file is None:
                        artifact_file = img.name
            if artifact_html_content is None:
                for html_f in search_dir.glob("*.html"):
                    try:
                        artifact_html_content = html_f.read_text(encoding="utf-8")
                        break
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001
        pass

    if _plan_tmpdir is not None:
        try:
            shutil.rmtree(_plan_tmpdir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    completed = best_score >= quality_threshold and best_exec_ok
    tests_broken: tuple[str, ...] = ("pytest_failures",) if harness_total_failed > 0 else ()
    _reflexion_entry = ReflexionEntry(
        task_type="plan_loop",
        objective=objective,
        outcome="passed" if completed else "failed",
        what_worked=(
            f"lang={lang} best_score={best_score:.1f}/10 code_chars={len(best_code)}"
            if completed else "no iteration reached quality threshold"
        ),
        what_failed=(
            "none — quality threshold reached"
            if completed else
            f"best_score={best_score:.1f} never reached {quality_threshold} ({stop_reason}) | last_error={last_exec_output[:150]}"
        ),
        root_cause=(
            "n/a" if completed else
            (last_exec_output[:200] if last_exec_output else f"score capped at {final_score:.1f}/10")
        ),
        corrective_action=(
            f"Reuse this approach for {lang} tasks with similar objective: {objective[:80]}"
            if completed else
            f"Before retrying, retrieve past reflexions for plan_loop {lang} and avoid: {last_exec_output[:100]}"
        ),
        files_touched=(),
        tests_broken=tests_broken,
        validation_summary=(
            f"exec_ok on best iteration; pytest_failures={harness_total_failed}"
            if best_exec_ok else
            f"exec failed; pytest_failures={harness_total_failed}"
        ),
        repair_attempts_used=max(0, len(iterations) - 1),
        stop_reason=stop_reason,
        confidence=min(0.95, best_score / 10.0),
        task_id=topic,
        run_id=job_id,
    )
    try:
        persist_reflexion(_nemo_adapter, _reflexion_entry)
    except Exception:  # noqa: BLE001
        pass

    if iterations and best_score >= 7.0:
        try:
            ingest_task_outcome(
                _nemo_adapter,
                objective=objective,
                repo_path=_plan_repo if _plan_repo else "",
                files_changed=[],
                result_summary=(
                    f"plan_loop score={best_score:.1f}/10 "
                    f"iterations={len(iterations)} lang={lang} stop_reason={stop_reason}"
                ),
                success=completed,
            )
        except Exception:
            pass

    if completed and best_code and best_score >= 7.0:
        try:
            _nemo_adapter.call(
                NemoLifecyclePhase.REVIEW,
                "cognitive_ingest",
                content=(
                    f"[plan_loop_success][{lang}] objective={objective!r} score={best_score:.1f}\n"
                    f"```{lang}\n{best_code[:800]}\n```"
                ),
                memory_type="plan_loop_success",
                importance_level=9,
                tags=["plan_loop_success", lang, "code_pattern"],
            )
        except Exception:
            pass

    yield {
        "type": "done",
        "ok": True,
        "objective": objective,
        "iterations_run": len(iterations),
        "final_score": best_score,
        "best_score": best_score,
        "stop_reason": stop_reason,
        "quality_threshold": quality_threshold,
        "completed": completed,
        "artifact_file": artifact_file,
        "artifact_html_content": artifact_html_content,
        "iterations": [
            {
                "iteration": it["iteration"],
                "score": it["score"],
                "exec_ok": it["exec_ok"],
                "exec_output": it["exec_output"],
                "critique_summary": it["critique_text"][:300],
                "code_chars": len(it["code"]),
                "harness": it["harness"] if not (it.get("harness") or {}).get("skipped") else None,
                "sdd_phase": it.get("sdd_phase"),
            }
            for it in iterations
        ],
        "final_sdd_phase": str(_sdd_phase),
        "final_code": iterations[-1]["code"] if iterations else "",
        "final_critique": iterations[-1]["critique_text"] if iterations else "",
        "tool_calls": tool_calls,
    }
    if job_id:
        with _plan_jobs_lock:
            _plan_jobs.pop(job_id, None)


def api_agent_plan(
    config: "MissionControlServerConfig",
    payload: dict[str, object],
) -> dict[str, object]:
    """Non-streaming wrapper — collects generator events and returns the final done dict."""
    done: dict[str, object] = {}
    for event in api_agent_plan_gen(config, payload):
        if event.get("type") == "done":
            done = event
    return done if done else {"ok": False, "error": "plan loop produced no done event"}


def api_plan_cancel(payload: dict[str, object]) -> dict[str, object]:
    """Signal a running plan job to cancel after the current iteration."""
    job_id = str(payload.get("job_id") or "")
    with _plan_jobs_lock:
        if not job_id or job_id not in _plan_jobs:
            return {"ok": False, "error": "job not found"}
        _plan_jobs[job_id]["cancel"] = True
    return {"ok": True, "job_id": job_id}


def api_plan_steer(payload: dict[str, object]) -> dict[str, object]:
    """Inject a user directive into the next iteration of a running plan job."""
    job_id = str(payload.get("job_id") or "")
    directive = str(payload.get("directive") or "").strip()
    if not directive:
        return {"ok": False, "error": "directive is required"}
    with _plan_jobs_lock:
        if not job_id or job_id not in _plan_jobs:
            return {"ok": False, "error": "job not found"}
        _plan_jobs[job_id]["steer"] = directive
    return {"ok": True, "job_id": job_id, "directive": directive}
