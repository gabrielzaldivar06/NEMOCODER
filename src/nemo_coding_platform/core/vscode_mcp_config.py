from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VSCODE_STDIO_NEMO_URL = "stdio://vscode/nemo"


@dataclass(frozen=True, slots=True)
class VscodeMcpServerConfig:
    name: str
    command: str
    args: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] | None = None
    source_path: str = ""


def vscode_mcp_config_paths(repo_path: Path | None = None) -> tuple[Path, ...]:
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE")
    if appdata:
        paths.extend(
            [
                Path(appdata) / "Code" / "User" / "mcp.json",
                Path(appdata) / "Code - Insiders" / "User" / "mcp.json",
            ]
        )
    if userprofile:
        paths.append(Path(userprofile) / ".config" / "Code" / "User" / "mcp.json")
    if repo_path is not None:
        paths.append(repo_path / ".vscode" / "mcp.json")
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def discover_vscode_mcp_server(name: str = "nemo", repo_path: Path | None = None) -> VscodeMcpServerConfig | None:
    for path in vscode_mcp_config_paths(repo_path):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        server = _server_entry(data, name)
        if not isinstance(server, dict):
            continue
        command = server.get("command")
        args = server.get("args")
        if not isinstance(command, str) or not command.strip():
            continue
        if args is None:
            args_tuple: tuple[str, ...] = ()
        elif isinstance(args, list):
            args_tuple = tuple(str(item) for item in args)
        else:
            continue
        cwd_value = server.get("cwd")
        env_value = server.get("env")
        env = {str(key): str(value) for key, value in env_value.items()} if isinstance(env_value, dict) else None
        return VscodeMcpServerConfig(
            name=name,
            command=command.strip(),
            args=args_tuple,
            cwd=str(cwd_value) if isinstance(cwd_value, str) and cwd_value.strip() else None,
            env=env,
            source_path=str(path),
        )
    return None


_NEMO_SSE_BASE = "http://127.0.0.1:8765"


def _nemo_sse_available() -> bool:
    try:
        with urllib.request.urlopen(f"{_NEMO_SSE_BASE}/health", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def default_nemo_mcp_url(repo_path: Path | None = None) -> str:
    env_url = os.environ.get("SPACE_CODE_NEMO_MCP_URL")
    if env_url:
        return env_url
    # Prefer the persistent SSE server — no subprocess spawn per call
    if _nemo_sse_available():
        return f"{_NEMO_SSE_BASE}/mcp/sse"
    # Fall back to VS Code stdio bridge if SSE is not up
    if discover_vscode_mcp_server("nemo", repo_path) is not None:
        return VSCODE_STDIO_NEMO_URL
    return f"{_NEMO_SSE_BASE}/mcp/sse"


def _server_entry(data: dict[str, Any], name: str) -> object:
    servers = data.get("servers")
    if isinstance(servers, dict) and name in servers:
        return servers.get(name)
    mcp_servers = data.get("mcpServers")
    if isinstance(mcp_servers, dict) and name in mcp_servers:
        return mcp_servers.get(name)
    return None
