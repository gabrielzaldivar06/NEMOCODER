# Browser Agent + Credential Vault — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autonomous Playwright browser agent with VLM visual reasoning and a DPAPI-encrypted credential vault to the Space Code Mission Control backend and frontend.

**Architecture:** Two new Python modules (`credential_vault.py`, `browser_agent.py`) plus new API routes in `mission_control_server.py` and frontend additions in `main.tsx`/`styles.css`. The vault stores credentials encrypted with Windows DPAPI (XOR fallback on non-Windows). The browser agent runs Playwright headless in a thread, loops up to 20 steps, calls the local VLM for each screenshot, and streams SSE events to the frontend.

**Tech Stack:** Python `win32crypt` (DPAPI), SQLite, `playwright.sync_api`, LM Studio VLM at `http://localhost:1234/v1`, React/TypeScript frontend, existing SSE infrastructure pattern in `_handle_plan_sse`.

---

## File Map

| File | Action |
|------|--------|
| `src/nemo_coding_platform/credential_vault.py` | **Create** — DPAPI encrypt/decrypt + SQLite CRUD (vault_create, vault_list, vault_update, vault_delete, vault_lookup) |
| `src/nemo_coding_platform/browser_agent.py` | **Create** — BrowserSession dataclass, TTL cleanup daemon, `_is_dangerous`, `_take_screenshot`, `_vlm_action`, `execute_browser_task` generator |
| `src/nemo_coding_platform/mission_control_server.py` | **Modify** — vault API routes (GET list, POST create/update/delete/lookup), browser SSE endpoint `_handle_browser_task_sse`, browser confirm/cancel/sessions endpoints, `_resolve_vlm_model`, `_AGENT_TOOL_CATALOG` tool #4, `do_GET`/`do_POST`/`do_PUT` routing |
| `apps/mission-control/src/main.tsx` | **Modify** — `AgentActionKind` union + `browser_task` branch in `runAgentAction`, `VaultPanel` component |
| `apps/mission-control/src/styles.css` | **Modify** — vault panel + browser task CSS |
| `tests/test_credential_vault.py` | **Create** — vault unit tests (no network, uses tmp_path) |
| `tests/test_browser_agent.py` | **Create** — browser agent unit tests (mocked Playwright) |

**Note:** The existing server uses only GET/POST HTTP methods. Vault mutations (update, delete) are POST endpoints (`/api/vault/credentials/update`, `/api/vault/credentials/delete`) to match the established pattern.

---

## Task 1 — `credential_vault.py`: DPAPI Encrypt/Decrypt + SQLite CRUD

**Files:**
- Create: `src/nemo_coding_platform/credential_vault.py`
- Create: `tests/test_credential_vault.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_credential_vault.py
import pytest
from pathlib import Path
from nemo_coding_platform.credential_vault import (
    vault_encrypt,
    vault_decrypt,
    vault_create,
    vault_list,
    vault_lookup,
    vault_update,
    vault_delete,
)


def test_encrypt_decrypt_roundtrip():
    blob = vault_encrypt("secret123")
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    assert vault_decrypt(blob) == "secret123"


def test_encrypt_produces_different_bytes_than_plaintext():
    blob = vault_encrypt("hello")
    assert blob != b"hello"


def test_create_and_list(tmp_path):
    db = tmp_path / "vault.db"
    result = vault_create(db, "github", "user1", "pass1", "github.com")
    assert result["alias"] == "github"
    assert "id" in result
    assert "created_at" in result
    rows = vault_list(db)
    assert len(rows) == 1
    assert rows[0]["alias"] == "github"
    assert rows[0]["url_pattern"] == "github.com"
    assert "username" not in rows[0]
    assert "password" not in rows[0]
    assert rows[0]["has_notes"] == 0


def test_create_with_notes(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "gmail", "me@gmail.com", "hunter2", notes="personal account")
    rows = vault_list(db)
    assert rows[0]["has_notes"] == 1


def test_lookup_returns_plaintext(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "github", "myuser", "mypass")
    creds = vault_lookup(db, "github")
    assert creds["username"] == "myuser"
    assert creds["password"] == "mypass"


def test_lookup_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_lookup(db, "nonexistent")


def test_update_password(tmp_path):
    db = tmp_path / "vault.db"
    r = vault_create(db, "github", "user1", "pass1")
    vault_update(db, r["id"], password="newpass")
    creds = vault_lookup(db, "github")
    assert creds["password"] == "newpass"
    assert creds["username"] == "user1"


def test_update_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_update(db, "nonexistent-id", alias="x")


def test_delete(tmp_path):
    db = tmp_path / "vault.db"
    r = vault_create(db, "github", "u", "p")
    vault_delete(db, r["id"])
    assert vault_list(db) == []


def test_delete_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_delete(db, "nonexistent-id")


def test_alias_uniqueness_enforced(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "github", "u1", "p1")
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        vault_create(db, "github", "u2", "p2")


def test_list_ordered_by_alias(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "zoom", "u", "p")
    vault_create(db, "aws", "u", "p")
    vault_create(db, "github", "u", "p")
    aliases = [r["alias"] for r in vault_list(db)]
    assert aliases == ["aws", "github", "zoom"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd c:\dev\dev4
.\.venv\Scripts\python.exe -m pytest tests/test_credential_vault.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'nemo_coding_platform.credential_vault'`

- [ ] **Step 3: Implement `credential_vault.py`**

```python
# src/nemo_coding_platform/credential_vault.py
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def vault_encrypt(plaintext: str) -> bytes:
    """Encrypt plaintext using Windows DPAPI; falls back to XOR obfuscation."""
    try:
        import win32crypt  # type: ignore[import]
        return win32crypt.CryptProtectData(plaintext.encode(), None, None, None, None, 0)
    except ImportError:
        import socket
        key = socket.gethostname().encode() or b"spacecode"
        data = plaintext.encode()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def vault_decrypt(blob: bytes) -> str:
    """Decrypt a vault blob; mirrors vault_encrypt fallback logic."""
    try:
        import win32crypt  # type: ignore[import]
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode()
    except ImportError:
        import socket
        key = socket.gethostname().encode() or b"spacecode"
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(blob)).decode()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id           TEXT PRIMARY KEY,
    alias        TEXT NOT NULL UNIQUE,
    username_enc BLOB NOT NULL,
    password_enc BLOB NOT NULL,
    url_pattern  TEXT,
    notes_enc    BLOB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
"""


def _conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def vault_list(db_path: Path) -> list[dict]:
    """Return all credentials without decrypting sensitive fields."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, alias, url_pattern, "
            "(notes_enc IS NOT NULL) AS has_notes, created_at, updated_at "
            "FROM credentials ORDER BY alias"
        ).fetchall()
    return [dict(r) for r in rows]


def vault_create(
    db_path: Path,
    alias: str,
    username: str,
    password: str,
    url_pattern: str = "",
    notes: str = "",
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cred_id = uuid.uuid4().hex
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO credentials "
            "(id, alias, username_enc, password_enc, url_pattern, notes_enc, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cred_id,
                alias,
                vault_encrypt(username),
                vault_encrypt(password),
                url_pattern or None,
                vault_encrypt(notes) if notes else None,
                now,
                now,
            ),
        )
        conn.commit()
    return {"id": cred_id, "alias": alias, "created_at": now}


def vault_update(db_path: Path, cred_id: str, **fields: object) -> dict:
    """Update one or more fields of a credential. Pass only fields to change."""
    now = datetime.now(timezone.utc).isoformat()
    updates: list[str] = []
    params: list[object] = []
    if "alias" in fields:
        updates.append("alias = ?")
        params.append(fields["alias"])
    if "username" in fields:
        updates.append("username_enc = ?")
        params.append(vault_encrypt(str(fields["username"])))
    if "password" in fields:
        updates.append("password_enc = ?")
        params.append(vault_encrypt(str(fields["password"])))
    if "url_pattern" in fields:
        updates.append("url_pattern = ?")
        params.append(fields["url_pattern"] or None)
    if "notes" in fields:
        val = str(fields["notes"]) if fields["notes"] else None
        updates.append("notes_enc = ?")
        params.append(vault_encrypt(val) if val else None)
    if not updates:
        return {"id": cred_id, "updated_at": now}
    updates.append("updated_at = ?")
    params.append(now)
    params.append(cred_id)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"UPDATE credentials SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(cred_id)
    return {"id": cred_id, "updated_at": now}


def vault_delete(db_path: Path, cred_id: str) -> None:
    with _conn(db_path) as conn:
        cur = conn.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(cred_id)


def vault_lookup(db_path: Path, alias: str) -> dict:
    """Return decrypted {username, password} for an alias. Internal use only."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT username_enc, password_enc FROM credentials WHERE alias = ?",
            (alias,),
        ).fetchone()
    if row is None:
        raise KeyError(alias)
    return {
        "username": vault_decrypt(bytes(row["username_enc"])),
        "password": vault_decrypt(bytes(row["password_enc"])),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\.venv\Scripts\python.exe -m pytest tests/test_credential_vault.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```
git add src/nemo_coding_platform/credential_vault.py tests/test_credential_vault.py
git commit -m "feat: add credential vault with DPAPI encrypt/decrypt and SQLite CRUD"
```

---

## Task 2 — Vault API Routes in `mission_control_server.py`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

The vault DB path is `config.runtimes_path / "mission-control" / "vault.db"`.

Endpoints added (all POST for consistency with the existing server pattern):
- `GET  /api/vault/credentials` → list (no decryption)
- `POST /api/vault/credentials` → create
- `POST /api/vault/credentials/update` → update {id, ...fields}
- `POST /api/vault/credentials/delete` → delete {id}
- `POST /api/vault/credentials/lookup` → internal-only {alias} → {username, password}

- [ ] **Step 1: Add the five vault API handler functions**

Find the `api_generate_image` function in `mission_control_server.py` (search for `def api_generate_image`). Insert the following block immediately after that function ends (before the next `def`):

```python
# ---------------------------------------------------------------------------
# Credential Vault API
# ---------------------------------------------------------------------------

def _vault_db(config: MissionControlServerConfig) -> Path:
    return config.runtimes_path / "mission-control" / "vault.db"


def api_vault_list(config: MissionControlServerConfig) -> dict:
    from nemo_coding_platform.credential_vault import vault_list
    return {"credentials": vault_list(_vault_db(config))}


def api_vault_create(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_create
    alias = str(payload.get("alias") or "").strip()
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not alias:
        raise _bad_request("alias is required", error_code="missing_alias")
    if not username or not password:
        raise _bad_request("username and password are required", error_code="missing_credentials")
    try:
        result = vault_create(
            _vault_db(config),
            alias,
            username,
            password,
            str(payload.get("url_pattern") or ""),
            str(payload.get("notes") or ""),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise _bad_request(f"Alias '{alias}' already exists", error_code="duplicate_alias") from exc
        raise
    return result


def api_vault_update(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_update
    cred_id = str(payload.get("id") or "").strip()
    if not cred_id:
        raise _bad_request("id is required", error_code="missing_id")
    fields = {k: payload[k] for k in ("alias", "username", "password", "url_pattern", "notes") if k in payload}
    try:
        return vault_update(_vault_db(config), cred_id, **fields)
    except KeyError:
        raise _bad_request(f"Credential {cred_id} not found", error_code="not_found", status_code=404)


def api_vault_delete(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_delete
    cred_id = str(payload.get("id") or "").strip()
    if not cred_id:
        raise _bad_request("id is required", error_code="missing_id")
    try:
        vault_delete(_vault_db(config), cred_id)
    except KeyError:
        raise _bad_request(f"Credential {cred_id} not found", error_code="not_found", status_code=404)
    return {"ok": True}


def api_vault_lookup(config: MissionControlServerConfig, payload: dict, client_address: str) -> dict:
    """Internal-only: decrypts and returns credentials. Only callable from 127.0.0.1."""
    if not client_address.startswith("127."):
        raise _bad_request("Forbidden", error_code="forbidden", status_code=403)
    from nemo_coding_platform.credential_vault import vault_lookup
    alias = str(payload.get("alias") or "").strip()
    if not alias:
        raise _bad_request("alias is required", error_code="missing_alias")
    try:
        return vault_lookup(_vault_db(config), alias)
    except KeyError:
        raise _bad_request(f"Alias '{alias}' not found", error_code="not_found", status_code=404)
```

- [ ] **Step 2: Add vault routes to `do_GET`**

In `do_GET`, find the block that ends with:
```python
        if route != "/api/state":
            _json_response(self, 404, {"error": "not_found"})
            return
        self._handle(lambda _: api_state(self.server.config, self.server.jobs), {})
```

Insert before that block:
```python
        if route == "/api/vault/credentials":
            self._handle(lambda _: api_vault_list(self.server.config), {})
            return
```

- [ ] **Step 3: Add vault routes to `do_POST` handler dict**

In `do_POST`, find the `handlers` dict. Add these entries (after the `/api/agent/generate-image` line):
```python
            "/api/vault/credentials": lambda payload: api_vault_create(self.server.config, payload),
            "/api/vault/credentials/update": lambda payload: api_vault_update(self.server.config, payload),
            "/api/vault/credentials/delete": lambda payload: api_vault_delete(self.server.config, payload),
            "/api/vault/credentials/lookup": lambda payload: api_vault_lookup(self.server.config, payload, self.client_address[0]),
```

- [ ] **Step 4: Smoke-test manually (no automated test for HTTP routing)**

Start the backend:
```powershell
.\scripts\start-mvp-local.ps1
```

Then:
```powershell
# Create
curl -s -X POST http://127.0.0.1:8787/api/vault/credentials `
  -H "Content-Type: application/json" `
  -d '{"alias":"test","username":"myuser","password":"mypass"}' | python -m json.tool

# List (should show alias but NOT username/password)
curl -s http://127.0.0.1:8787/api/vault/credentials | python -m json.tool

# Delete (paste the id from create response)
curl -s -X POST http://127.0.0.1:8787/api/vault/credentials/delete `
  -H "Content-Type: application/json" `
  -d '{"id":"<id-from-above>"}' | python -m json.tool
```

Expected: create → `{"id": "...", "alias": "test", "created_at": "..."}`. List → credentials array with alias but no username/password. Delete → `{"ok": true}`.

- [ ] **Step 5: Commit**

```
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add credential vault API endpoints (list/create/update/delete/lookup)"
```

---

## Task 3 — `browser_agent.py`: Session Management, Screenshot, VLM Loop, Guardrails

**Files:**
- Create: `src/nemo_coding_platform/browser_agent.py`
- Create: `tests/test_browser_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_browser_agent.py
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

from nemo_coding_platform.browser_agent import (
    _is_dangerous,
    _browser_sessions,
    _session_lock,
    BrowserSession,
    execute_browser_task,
)


# ---------------------------------------------------------------------------
# _is_dangerous
# ---------------------------------------------------------------------------

def test_is_dangerous_submit_in_target():
    assert _is_dangerous({"action": "click", "target": "submit", "value": ""})


def test_is_dangerous_buy_in_value():
    assert _is_dangerous({"action": "fill", "target": "#q", "value": "buy now"})


def test_is_dangerous_safe_target():
    assert not _is_dangerous({"action": "click", "target": "Search", "value": ""})


def test_is_dangerous_checkout_in_target():
    assert _is_dangerous({"action": "click", "target": "Checkout", "value": ""})


def test_is_dangerous_case_insensitive():
    assert _is_dangerous({"action": "click", "target": "SUBMIT button", "value": ""})


# ---------------------------------------------------------------------------
# execute_browser_task — VLM returns "done" immediately
# ---------------------------------------------------------------------------

def _make_pw_mocks():
    """Build playwright context manager mock."""
    page = MagicMock()
    page.title.return_value = "Test Page"
    context = MagicMock()
    browser = MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page

    pw_obj = MagicMock()
    pw_obj.chromium.launch.return_value = browser

    pw_cm = MagicMock()
    pw_cm.__enter__ = MagicMock(return_value=pw_obj)
    pw_cm.__exit__ = MagicMock(return_value=False)
    return pw_cm, page


def test_execute_browser_task_vlm_done_on_first_step(tmp_path):
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "done", "target": "", "value": "", "reason": "task complete"},
         ):
        events = list(execute_browser_task(
            session_id="sess-done",
            url="https://example.com",
            task="find the title",
            credential_alias=None,
            max_steps=5,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    step_events = [e for e in events if e["type"] == "step"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(step_events) == 1
    assert done_events[0]["success"] is True
    assert done_events[0]["steps_taken"] == 1


def test_execute_browser_task_max_steps_reached(tmp_path):
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "click", "target": "#btn", "value": "", "reason": "keep going"},
         ):
        events = list(execute_browser_task(
            session_id="sess-max",
            url="https://example.com",
            task="click forever",
            credential_alias=None,
            max_steps=3,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"][0]
    assert done["success"] is False
    assert done["reason"] == "max_steps_reached"
    assert done["steps_taken"] == 3


def test_execute_browser_task_cancel(tmp_path):
    """Session cancel flag stops the loop."""
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    call_count = 0

    def slow_vlm(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        # After first step, mark session as cancelled
        with _session_lock:
            for sess in _browser_sessions.values():
                sess.cancel = True
        return {"action": "click", "target": "#btn", "value": "", "reason": "step"}

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch("nemo_coding_platform.browser_agent._vlm_action", side_effect=slow_vlm):
        events = list(execute_browser_task(
            session_id="sess-cancel",
            url="https://example.com",
            task="do things",
            credential_alias=None,
            max_steps=10,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"]
    assert done[0]["reason"] == "cancelled"


def test_execute_browser_task_playwright_not_installed(tmp_path):
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}), \
         patch("nemo_coding_platform.browser_agent.sync_playwright", side_effect=ImportError("no playwright")):
        events = list(execute_browser_task(
            session_id="sess-nopw",
            url="https://example.com",
            task="test",
            credential_alias=None,
            max_steps=3,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model=None,
            vault_db_path=None,
        ))
    assert events[0]["type"] == "error"
    assert "Playwright" in events[0]["message"]


def test_max_steps_capped_at_20(tmp_path):
    pw_cm, _ = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "click", "target": "#b", "value": "", "reason": "x"},
         ):
        events = list(execute_browser_task(
            session_id="sess-cap",
            url="https://example.com",
            task="go",
            credential_alias=None,
            max_steps=999,  # should be capped at 20
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"][0]
    assert done["steps_taken"] == 20
```

- [ ] **Step 2: Run tests to verify they fail**

```
.\.venv\Scripts\python.exe -m pytest tests/test_browser_agent.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'nemo_coding_platform.browser_agent'`

- [ ] **Step 3: Implement `browser_agent.py`**

```python
# src/nemo_coding_platform/browser_agent.py
import base64
import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from uuid import uuid4

DANGER_KEYWORDS = frozenset({
    "submit", "comprar", "pagar", "pay", "buy", "purchase", "checkout",
    "delete", "eliminar", "remove", "unsubscribe", "confirm", "proceed",
    "send money", "transfer", "sign out", "logout",
})

_SESSION_TTL = 30 * 60  # seconds


@dataclass
class BrowserSession:
    session_id: str
    page: object
    browser: object
    context: object
    url: str = ""
    step_count: int = 0
    cancel: bool = False
    confirm_event: threading.Event = field(default_factory=threading.Event)
    confirm_approved: bool = False
    last_used: float = field(default_factory=time.time)


_browser_sessions: dict[str, BrowserSession] = {}
_session_lock = threading.Lock()


def _cleanup_expired() -> None:
    while True:
        time.sleep(300)
        now = time.time()
        with _session_lock:
            expired = [
                sid for sid, s in _browser_sessions.items()
                if now - s.last_used > _SESSION_TTL
            ]
        for sid in expired:
            _close_session(sid)


threading.Thread(
    target=_cleanup_expired, daemon=True, name="browser-session-cleanup"
).start()


def _close_session(session_id: str) -> None:
    with _session_lock:
        sess = _browser_sessions.pop(session_id, None)
    if sess is None:
        return
    for obj in (sess.page, sess.context, sess.browser):
        try:
            obj.close()
        except Exception:
            pass


def _is_dangerous(action: dict) -> bool:
    target = str(action.get("target") or "").lower()
    value = str(action.get("value") or "").lower()
    return any(kw in target or kw in value for kw in DANGER_KEYWORDS)


def _take_screenshot(page: object, artifacts_dir: Path, step: int) -> tuple[str, str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    fname = f"browser-step-{step:03d}-{uuid4().hex[:6]}.png"
    fpath = artifacts_dir / fname
    try:
        mask = page.locator("input[type=password]")
        page.screenshot(path=str(fpath), mask=[mask], full_page=False, timeout=15000)
    except Exception:
        # Screenshot failed — write an empty file so the URL still resolves
        fpath.write_bytes(b"")
    return str(fpath), f"/api/artifacts/image/{fname}"


def _vlm_action(
    screenshot_b64: str,
    task: str,
    history: list[dict],
    base_url: str,
    model: str,
) -> dict:
    prompt = (
        f"You are a web navigation agent. Your task: {task}\n"
        f"Steps taken so far: {json.dumps(history[-5:])}\n"
        "Look at the screenshot and respond ONLY with valid JSON (no explanation):\n"
        '{"action": "fill|click|navigate|scroll|wait|done", '
        '"target": "CSS selector OR visible text to match", '
        '"value": "only for fill action", '
        '"reason": "one line"}\n'
        'Action "done" means the task is complete.'
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": 256,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    # Strip reasoning tags from thinking models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<\|thinking\|>.*?<\|/thinking\|>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"action": "done", "target": "", "value": "", "reason": "VLM returned unparseable response"}


def execute_browser_task(
    session_id: str,
    url: str,
    task: str,
    credential_alias: str | None,
    max_steps: int,
    artifacts_dir: Path,
    base_url: str,
    vlm_model: str | None,
    vault_db_path: Path | None,
) -> Iterator[dict]:
    max_steps = min(max_steps, 20)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        yield {
            "type": "error",
            "message": (
                "Playwright no disponible. "
                "Instalar: pip install playwright && python -m playwright install chromium"
            ),
        }
        return

    # Resolve credentials — kept in local variable, never yielded or logged
    task_context = task
    if credential_alias and vault_db_path:
        try:
            from nemo_coding_platform.credential_vault import vault_lookup
            creds = vault_lookup(vault_db_path, credential_alias)
            task_context = (
                f"{task}\n"
                f"[CREDENTIALS for '{credential_alias}': "
                f"username='{creds['username']}', password='{creds['password']}' — "
                "fill these when login fields appear; never include them in reason fields]"
            )
            del creds
        except KeyError:
            yield {"type": "error", "message": f"Credential alias '{credential_alias}' not found in vault"}
            return

    history: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()

        sess = BrowserSession(
            session_id=session_id,
            page=page,
            browser=browser,
            context=ctx,
            url=url,
        )
        with _session_lock:
            _browser_sessions[session_id] = sess

        try:
            page.goto(url, timeout=30000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)

            for step in range(1, max_steps + 1):
                # Check cancel flag before each step
                with _session_lock:
                    if _browser_sessions.get(session_id, sess).cancel:
                        yield {
                            "type": "done",
                            "steps_taken": step - 1,
                            "success": False,
                            "reason": "cancelled",
                            "session_id": session_id,
                        }
                        return

                sess.last_used = time.time()
                sess.step_count = step

                _, screenshot_url = _take_screenshot(page, artifacts_dir, step)
                screenshot_path = artifacts_dir / screenshot_url.split("/")[-1]
                try:
                    screenshot_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
                except OSError:
                    screenshot_b64 = ""

                page_title = page.title()

                if vlm_model and screenshot_b64:
                    try:
                        action = _vlm_action(screenshot_b64, task_context, history, base_url, vlm_model)
                    except Exception as exc:
                        action = {"action": "done", "target": "", "value": "", "reason": f"VLM error: {exc}"}
                else:
                    action = {"action": "wait", "target": "", "value": "", "reason": "no VLM — screenshot-only mode"}

                # Strip password values from emitted events
                safe_action = {k: v for k, v in action.items()}
                if action.get("action") == "fill" and str(action.get("target", "")).lower().find("password") >= 0:
                    safe_action["value"] = "••••••"

                yield {
                    "type": "step",
                    "step": step,
                    "action_taken": safe_action,
                    "screenshot_url": screenshot_url,
                    "page_title": page_title,
                    "session_id": session_id,
                }

                if action.get("action") == "done":
                    _, final_url = _take_screenshot(page, artifacts_dir, step + 1)
                    yield {
                        "type": "done",
                        "steps_taken": step,
                        "success": True,
                        "final_screenshot_url": final_url,
                        "session_id": session_id,
                    }
                    return

                # Pause for user confirmation on dangerous actions
                if _is_dangerous(action):
                    yield {
                        "type": "pause_required",
                        "step": step,
                        "action": safe_action,
                        "reason": str(action.get("reason") or ""),
                        "session_id": session_id,
                    }
                    with _session_lock:
                        current = _browser_sessions.get(session_id)
                    if current:
                        current.confirm_event.clear()
                        confirmed = current.confirm_event.wait(timeout=120)
                        approved = confirmed and current.confirm_approved
                    else:
                        approved = False
                    if not approved:
                        yield {
                            "type": "done",
                            "steps_taken": step,
                            "success": False,
                            "reason": "user_rejected_action",
                            "session_id": session_id,
                        }
                        return

                # Execute the action (30s timeout per action)
                act = str(action.get("action") or "")
                target = str(action.get("target") or "")
                value = str(action.get("value") or "")
                try:
                    if act == "click":
                        page.click(target, timeout=30000)
                    elif act == "fill":
                        page.fill(target, value, timeout=30000)
                    elif act == "navigate":
                        page.goto(target, timeout=30000)
                    elif act == "scroll":
                        page.evaluate("window.scrollBy(0, 400)")
                    elif act == "wait":
                        time.sleep(2)
                    history.append({"step": step, "action": act, "target": target, "reason": str(action.get("reason") or "")})
                except Exception as exc:
                    history.append({"step": step, "action": act, "target": target, "error": str(exc)})

            # max_steps exhausted
            _, final_url = _take_screenshot(page, artifacts_dir, max_steps + 1)
            yield {
                "type": "done",
                "steps_taken": max_steps,
                "success": False,
                "reason": "max_steps_reached",
                "final_screenshot_url": final_url,
                "session_id": session_id,
            }

        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            with _session_lock:
                _browser_sessions.pop(session_id, None)


def get_sessions_info() -> list[dict]:
    with _session_lock:
        return [
            {
                "session_id": s.session_id,
                "step_count": s.step_count,
                "last_used": s.last_used,
                "url": s.url,
            }
            for s in _browser_sessions.values()
        ]


def confirm_action(session_id: str, approved: bool) -> bool:
    with _session_lock:
        sess = _browser_sessions.get(session_id)
    if sess is None:
        return False
    sess.confirm_approved = approved
    sess.confirm_event.set()
    return True


def cancel_session(session_id: str) -> bool:
    with _session_lock:
        sess = _browser_sessions.get(session_id)
    if sess is None:
        return False
    sess.cancel = True
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\.venv\Scripts\python.exe -m pytest tests/test_browser_agent.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```
git add src/nemo_coding_platform/browser_agent.py tests/test_browser_agent.py
git commit -m "feat: add browser agent with VLM loop, session management, and guardrails"
```

---

## Task 4 — Browser SSE Endpoint + Confirm/Cancel/Sessions in `mission_control_server.py`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

Adds `_handle_browser_task_sse` method and three POST/GET endpoints.

- [ ] **Step 1: Add `_resolve_vlm_model` helper function**

Find `def _resolve_lmstudio_model` in `mission_control_server.py`. Insert this function immediately after it ends (before `def _chat_model`):

```python
def _resolve_vlm_model(base_url: str) -> str | None:
    """Return the best available VLM model from LM Studio, or None if none loaded."""
    _SKIP = re.compile(r"embed|rerank|bge|nomic", re.I)
    base = base_url.rstrip("/")
    mgmt_base = re.sub(r"/v\d+$", "", base)
    try:
        req = urllib.request.Request(f"{mgmt_base}/api/v0/models", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        candidates = [
            m for m in data.get("data", [])
            if m.get("type") == "vlm"
            and m.get("state") == "loaded"
            and not _SKIP.search(m.get("id", ""))
        ]
        if candidates:
            candidates.sort(key=lambda m: int(m.get("loaded_context_length") or 0), reverse=True)
            return str(candidates[0]["id"])
    except Exception:
        pass
    return None
```

- [ ] **Step 2: Add four browser API handler functions**

Find `def api_vault_list` (added in Task 2). Insert the following block immediately after the vault functions:

```python
# ---------------------------------------------------------------------------
# Browser Agent API
# ---------------------------------------------------------------------------

def api_browser_task_start(
    config: MissionControlServerConfig,
    payload: dict,
) -> dict:
    """Validate browser-task payload and return session_id. Actual streaming via SSE."""
    url = str(payload.get("url") or "").strip()
    task = str(payload.get("task") or "").strip()
    if not url:
        raise _bad_request("url is required", error_code="missing_url")
    if not task:
        raise _bad_request("task is required", error_code="missing_task")
    session_id = str(payload.get("session_id") or f"browser-{uuid4().hex[:12]}")
    return {"session_id": session_id}


def api_browser_confirm(payload: dict) -> dict:
    from nemo_coding_platform.browser_agent import confirm_action
    session_id = str(payload.get("session_id") or "")
    approved = bool(payload.get("approved", False))
    if not session_id:
        raise _bad_request("session_id is required", error_code="missing_session_id")
    found = confirm_action(session_id, approved)
    if not found:
        raise _bad_request(f"Session {session_id} not found", error_code="not_found", status_code=404)
    return {"ok": True}


def api_browser_cancel(payload: dict) -> dict:
    from nemo_coding_platform.browser_agent import cancel_session
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        raise _bad_request("session_id is required", error_code="missing_session_id")
    cancel_session(session_id)
    return {"ok": True}


def api_browser_sessions(config: MissionControlServerConfig) -> dict:
    from nemo_coding_platform.browser_agent import get_sessions_info
    return {"sessions": get_sessions_info()}
```

- [ ] **Step 3: Add `_handle_browser_task_sse` method to the HTTP handler class**

Find the `_handle_plan_sse` method in `mission_control_server.py`. Insert the following method immediately after it (before `_handle_timeline_sse`):

```python
    def _handle_browser_task_sse(self, payload: dict[str, object]) -> None:
        """Stream browser agent steps as Server-Sent Events."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

        def _send(data: dict[str, object]) -> bool:
            try:
                line = ("data: " + json.dumps(data, sort_keys=True) + "\n\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        url = str(payload.get("url") or "").strip()
        task = str(payload.get("task") or "").strip()
        credential_alias = str(payload.get("credential_alias") or "").strip() or None
        max_steps = int(payload.get("max_steps") or 10)
        session_id = str(payload.get("session_id") or f"browser-{uuid4().hex[:12]}")

        if not url or not task:
            _send({"type": "error", "message": "url and task are required"})
            return

        config = self.server.config
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "images"
        base_url = str(payload.get("model_base_url") or "http://localhost:1234/v1")
        vlm_model = _resolve_vlm_model(base_url) or _resolve_lmstudio_model(base_url) or None
        vault_db = _vault_db(config) if credential_alias else None

        from nemo_coding_platform.browser_agent import execute_browser_task
        try:
            for event in execute_browser_task(
                session_id=session_id,
                url=url,
                task=task,
                credential_alias=credential_alias,
                max_steps=max_steps,
                artifacts_dir=artifacts_dir,
                base_url=base_url,
                vlm_model=vlm_model,
                vault_db_path=vault_db,
            ):
                if not _send(event):
                    break
        except Exception as exc:
            _send({"type": "error", "message": str(exc)})
```

- [ ] **Step 4: Wire the browser task SSE into `do_POST`**

In `do_POST`, find the early SSE dispatch block for the plan endpoint:
```python
        if urlparse(self.path).path == "/api/agent/plan" and "text/event-stream" in self.headers.get("Accept", ""):
```

Add immediately after that `if` block (but still before the `handlers` dict):
```python
        if urlparse(self.path).path == "/api/agent/browser-task" and "text/event-stream" in self.headers.get("Accept", ""):
            try:
                payload = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError):
                payload = {}
            self._handle_browser_task_sse(payload)
            return
```

- [ ] **Step 5: Add browser routes to `do_GET` and `do_POST`**

In `do_GET`, before the `if route != "/api/state":` sentinel, add:
```python
        if route == "/api/agent/browser-sessions":
            self._handle(lambda _: api_browser_sessions(self.server.config), {})
            return
```

In `do_POST` `handlers` dict, add after the vault entries:
```python
            "/api/agent/browser-task": lambda payload: api_browser_task_start(self.server.config, payload),
            "/api/agent/browser-confirm": lambda payload: api_browser_confirm(payload),
            "/api/agent/browser-cancel": lambda payload: api_browser_cancel(payload),
```

- [ ] **Step 6: Smoke-test the SSE endpoint**

Start the backend, then in a new PowerShell window:
```powershell
# This will stream events — Ctrl+C after a few seconds
curl -N -X POST http://127.0.0.1:8787/api/agent/browser-task `
  -H "Content-Type: application/json" `
  -H "Accept: text/event-stream" `
  -d '{"url":"https://example.com","task":"find the page title","max_steps":2}'
```

Expected: SSE events starting with `data: {"session_id":...` for `step` events, ending with a `done` event.

- [ ] **Step 7: Commit**

```
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add browser task SSE endpoint, VLM resolver, confirm/cancel/sessions APIs"
```

---

## Task 5 — Add `browser_task` to `_AGENT_TOOL_CATALOG` + `_llm_tool_call_to_action`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

The LLM should be able to suggest browser tasks via the tool catalog. The frontend already handles tool actions from the catalog via `_parse_llm_tool_calls` and `_llm_tool_call_to_action`.

- [ ] **Step 1: Extend `_AGENT_TOOL_CATALOG`**

Find `_AGENT_TOOL_CATALOG` in `mission_control_server.py` (around line 5147). It ends with something like:
```
CRITICAL: These 3 are the ONLY tools you can embed...
```

Change `"These 3 are the ONLY tools"` to `"These 4 are the ONLY tools"` and add tool #4 before the CRITICAL line:

```python
4. browser_task — autonomous web navigation with visual AI
   {"tool": "browser_task", "params": {
     "url": "https://...",
     "task": "full description of what to accomplish on the page",
     "credential_alias": "optional-alias-from-vault",
     "max_steps": 8
   }}
   Use when the user asks to navigate a website, log in somewhere, fill out a form, or interact with a web page.
   Credentials are resolved from the vault by alias — NEVER put actual passwords in params.

```

Also update the count in the header from `THE ONLY 3 TOOLS` to `THE ONLY 4 TOOLS`.

- [ ] **Step 2: Update `_llm_tool_call_to_action` to handle `browser_task`**

Find `def _llm_tool_call_to_action` in `mission_control_server.py`. Add a new `elif` branch for `browser_task`:

```python
    elif tool_name == "browser_task":
        params = inv.get("params") or {}
        url = str(params.get("url") or "").strip()
        task = str(params.get("task") or "").strip()
        if not url or not task:
            return None
        label = f"Browse: {task[:60]}"
        return AgentAction(
            kind="browser_task",
            label=label,
            payload={
                "url": url,
                "task": task,
                "credential_alias": str(params.get("credential_alias") or ""),
                "max_steps": int(params.get("max_steps") or 10),
            },
        )
```

- [ ] **Step 3: Verify TypeScript `AgentActionKind` won't break (frontend check)**

The frontend will need to handle `browser_task` kind in Task 6. For now, just check that the backend produces it correctly via a quick chat test (can be done after Task 6).

- [ ] **Step 4: Commit**

```
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add browser_task to agent tool catalog and llm_tool_call_to_action"
```

---

## Task 6 — Frontend: `browser_task` AgentAction + SSE Handling in `main.tsx`

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

The frontend needs to handle the `browser_task` AgentAction kind, which opens an SSE stream and renders screenshots as they arrive.

- [ ] **Step 1: Add `browser_task` to `AgentActionKind` union**

Find the `AgentActionKind` type definition in `main.tsx`. It looks like:
```typescript
type AgentActionKind = "run" | "plan_generate" | "plan_cancel" | "plan_steer" | ...
```

Add `"browser_task"` to the union.

- [ ] **Step 2: Add the `browser_task` branch to `runAgentAction`**

Find `runAgentAction` in `main.tsx`. After the existing `plan_generate` branch (or at the end of the switch/if-else chain), add:

```typescript
if (action.kind === "browser_task") {
  const { url, task, credential_alias, max_steps = 10 } = action.payload as {
    url: string;
    task: string;
    credential_alias?: string;
    max_steps?: number;
  };

  const sessionId = `browser-${Date.now()}`;
  let stopInjected = false;

  // Inject "Browsing..." status message into timeline
  const statusMsgId = `browser-status-${sessionId}`;
  appendTimelineEvent({
    id: statusMsgId,
    role: "assistant",
    content: `Navigating to ${url}...`,
    actions: [],
    timestamp: new Date().toISOString(),
  });

  // Fetch SSE stream (EventSource doesn't support POST, so use fetch + ReadableStream)
  fetch("/api/agent/browser-task", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ url, task, credential_alias, max_steps, session_id: sessionId }),
  }).then(async (res) => {
    if (!res.ok || !res.body) return;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    const processLine = (line: string) => {
      if (!line.startsWith("data: ")) return;
      try {
        const evt = JSON.parse(line.slice(6));

        if (evt.type === "step") {
          // Update status message with latest screenshot
          updateTimelineEvent(statusMsgId, {
            content: `Step ${evt.step}: ${evt.page_title || url}`,
            screenshotUrl: evt.screenshot_url,
          });
          // Inject Stop button on first step
          if (!stopInjected) {
            stopInjected = true;
            updateTimelineEvent(statusMsgId, {
              actions: [
                {
                  kind: "browser_cancel" as AgentActionKind,
                  label: "Stop Browser",
                  payload: { session_id: sessionId },
                },
              ],
            });
          }
        } else if (evt.type === "pause_required") {
          // Show confirmation dialog
          const confirmed = window.confirm(
            `Browser wants to perform a potentially sensitive action:\n\n"${evt.action?.reason || JSON.stringify(evt.action)}"\n\nAllow this action?`
          );
          fetch("/api/agent/browser-confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, approved: confirmed }),
          });
        } else if (evt.type === "done") {
          const successMsg = evt.success
            ? `Browser task complete in ${evt.steps_taken} step(s).`
            : `Browser task stopped: ${evt.reason || "done"} after ${evt.steps_taken} step(s).`;
          updateTimelineEvent(statusMsgId, {
            content: successMsg,
            screenshotUrl: evt.final_screenshot_url,
            actions: [],  // clear stop button
          });
        } else if (evt.type === "error") {
          updateTimelineEvent(statusMsgId, {
            content: `Browser error: ${evt.message}`,
            actions: [],
          });
        }
      } catch (_) { /* ignore parse errors */ }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      lines.forEach(processLine);
    }
  }).catch((err) => {
    updateTimelineEvent(statusMsgId, {
      content: `Browser connection error: ${err.message}`,
      actions: [],
    });
  });

  return;
}
```

**Note:** `appendTimelineEvent` and `updateTimelineEvent` refer to whatever the existing pattern is in `main.tsx` for injecting/updating timeline messages. Use the same helper functions used by the `plan_generate` branch. If the helpers have different names, adapt accordingly.

- [ ] **Step 3: Handle `browser_cancel` action kind**

In `runAgentAction`, add a branch for `browser_cancel`:

```typescript
if (action.kind === "browser_cancel") {
  const { session_id } = action.payload as { session_id: string };
  fetch("/api/agent/browser-cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id }),
  });
  return;
}
```

Also add `"browser_cancel"` to the `AgentActionKind` union.

- [ ] **Step 4: TypeScript type-check**

```
cd apps/mission-control
npx tsc --noEmit
```

Expected: 0 errors. Fix any type errors before proceeding (usually missing `screenshotUrl` in timeline event type — add it as `screenshotUrl?: string` to the event interface if needed).

- [ ] **Step 5: Commit**

```
git add apps/mission-control/src/main.tsx
git commit -m "feat: browser_task frontend — SSE stream, step screenshots, pause confirm, stop button"
```

---

## Task 7 — Frontend: VaultPanel UI + CSS

**Files:**
- Modify: `apps/mission-control/src/main.tsx`
- Modify: `apps/mission-control/src/styles.css`

The VaultPanel is a collapsible section that lists credentials and provides add/edit forms. It never displays passwords.

- [ ] **Step 1: Add the VaultPanel React component to `main.tsx`**

Add this component near the other panel components (e.g., after TelemetryColumn or at the end of the component definitions):

```tsx
// ---- Types ----
interface VaultCredential {
  id: string;
  alias: string;
  url_pattern: string | null;
  has_notes: number;
  created_at: string;
  updated_at: string;
}

interface VaultFormState {
  alias: string;
  username: string;
  password: string;
  url_pattern: string;
  notes: string;
}

const VAULT_FORM_EMPTY: VaultFormState = {
  alias: "", username: "", password: "", url_pattern: "", notes: "",
};

// ---- VaultPanel ----
function VaultPanel() {
  const [open, setOpen] = React.useState(false);
  const [credentials, setCredentials] = React.useState<VaultCredential[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [formOpen, setFormOpen] = React.useState(false);
  const [editId, setEditId] = React.useState<string | null>(null);
  const [form, setForm] = React.useState<VaultFormState>(VAULT_FORM_EMPTY);
  const [error, setError] = React.useState("");

  const loadCredentials = () => {
    setLoading(true);
    fetch("/api/vault/credentials")
      .then((r) => r.json())
      .then((d) => setCredentials(d.credentials ?? []))
      .catch(() => setCredentials([]))
      .finally(() => setLoading(false));
  };

  React.useEffect(() => {
    if (open) loadCredentials();
  }, [open]);

  const handleSave = async () => {
    setError("");
    const isEdit = editId !== null;
    const endpoint = isEdit
      ? "/api/vault/credentials/update"
      : "/api/vault/credentials";
    const body: Record<string, unknown> = {
      alias: form.alias,
      username: form.username,
      url_pattern: form.url_pattern,
      notes: form.notes,
    };
    if (isEdit) {
      body.id = editId;
      if (form.password) body.password = form.password;
    } else {
      body.password = form.password;
    }
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.error || "Save failed");
      return;
    }
    setFormOpen(false);
    setEditId(null);
    setForm(VAULT_FORM_EMPTY);
    loadCredentials();
  };

  const handleDelete = async (id: string, alias: string) => {
    if (!window.confirm(`Delete credential "${alias}"?`)) return;
    await fetch("/api/vault/credentials/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    loadCredentials();
  };

  const handleEdit = (cred: VaultCredential) => {
    setEditId(cred.id);
    setForm({
      alias: cred.alias,
      username: "",         // never pre-fill
      password: "",         // never pre-fill
      url_pattern: cred.url_pattern ?? "",
      notes: "",
    });
    setFormOpen(true);
  };

  return (
    <div className="vault-panel">
      <button
        className="vault-panel-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "▾" : "▸"} Credential Vault
      </button>

      {open && (
        <div className="vault-panel-body">
          {loading && <div className="vault-loading">Loading...</div>}

          {credentials.map((cred) => (
            <div key={cred.id} className="vault-credential-row">
              <span className="vault-credential-alias">{cred.alias}</span>
              {cred.url_pattern && (
                <span className="vault-credential-url">{cred.url_pattern}</span>
              )}
              <span className="vault-credential-mask">••••••</span>
              <button className="vault-btn" onClick={() => handleEdit(cred)}>Edit</button>
              <button className="vault-btn vault-btn-danger" onClick={() => handleDelete(cred.id, cred.alias)}>Delete</button>
            </div>
          ))}

          {credentials.length === 0 && !loading && (
            <div className="vault-empty">No credentials stored yet.</div>
          )}

          {!formOpen ? (
            <button
              className="vault-btn vault-btn-add"
              onClick={() => { setFormOpen(true); setEditId(null); setForm(VAULT_FORM_EMPTY); setError(""); }}
            >
              + Add Credential
            </button>
          ) : (
            <div className="vault-form">
              <div className="vault-form-title">{editId ? "Edit Credential" : "Add Credential"}</div>
              {error && <div className="vault-form-error">{error}</div>}
              <label>Alias (required)
                <input
                  value={form.alias}
                  onChange={(e) => setForm((f) => ({ ...f, alias: e.target.value }))}
                  placeholder="e.g. github"
                  disabled={!!editId}
                />
              </label>
              <label>Username
                <input
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  placeholder="Username or email"
                />
              </label>
              <label>{editId ? "New Password (leave blank to keep current)" : "Password"}
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="Password"
                  autoComplete="new-password"
                />
              </label>
              <label>URL Pattern (optional)
                <input
                  value={form.url_pattern}
                  onChange={(e) => setForm((f) => ({ ...f, url_pattern: e.target.value }))}
                  placeholder="e.g. github.com"
                />
              </label>
              <label>Notes (optional)
                <input
                  value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  placeholder="Optional notes"
                />
              </label>
              <div className="vault-form-actions">
                <button className="vault-btn vault-btn-save" onClick={handleSave}>Save</button>
                <button className="vault-btn" onClick={() => { setFormOpen(false); setEditId(null); setError(""); }}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Mount `VaultPanel` in the UI**

Find where `TelemetryColumn` or the right-side panel is rendered. Add `<VaultPanel />` as a sibling below it. The exact location depends on the current layout — look for where `<TelemetryColumn` is rendered in the JSX and add immediately after it:

```tsx
<VaultPanel />
```

- [ ] **Step 3: Add browser screenshot rendering to timeline events**

If the timeline event type has a `screenshotUrl` field (added in Task 6), render it in the event display. Find where timeline events are rendered (look for `evt.content` in the JSX). Add screenshot rendering:

```tsx
{evt.screenshotUrl && (
  <div className="browser-task-screenshot">
    <img
      src={evt.screenshotUrl}
      alt={`Browser step`}
      className="browser-screenshot-img"
    />
  </div>
)}
```

- [ ] **Step 4: Add CSS to `styles.css`**

```css
/* ---- Credential Vault ---- */
.vault-panel {
  border: 1px solid #1e3a5f;
  border-radius: 6px;
  margin: 8px 0;
  background: #050d1a;
}

.vault-panel-toggle {
  width: 100%;
  padding: 8px 12px;
  text-align: left;
  background: transparent;
  border: none;
  color: #93c5fd;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  text-transform: uppercase;
}

.vault-panel-toggle:hover { background: #0a1628; }

.vault-panel-body { padding: 8px 12px 12px; }

.vault-credential-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid #0f2040;
  font-size: 0.82rem;
}

.vault-credential-alias { font-weight: 700; color: #e2e8f0; min-width: 80px; }
.vault-credential-url { color: #64748b; flex: 1; font-size: 0.78rem; }
.vault-credential-mask { color: #334155; opacity: 0.6; }

.vault-btn {
  padding: 3px 10px;
  font-size: 0.75rem;
  border: 1px solid #1e3a5f;
  background: #0a1628;
  color: #93c5fd;
  border-radius: 4px;
  cursor: pointer;
}
.vault-btn:hover { background: #0f2040; }
.vault-btn-danger { border-color: #7f1d1d; color: #fca5a5; }
.vault-btn-danger:hover { background: #200a0a; }
.vault-btn-add { border-color: #065f46; color: #6ee7b7; margin-top: 8px; }
.vault-btn-save { border-color: #065f46; background: #022c22; color: #6ee7b7; }
.vault-empty { color: #334155; font-size: 0.8rem; padding: 8px 0; }
.vault-loading { color: #475569; font-size: 0.8rem; }

.vault-form {
  border: 1px solid #1e3a5f;
  border-radius: 4px;
  padding: 12px;
  margin-top: 8px;
  background: #080f1f;
}
.vault-form-title { font-size: 0.82rem; font-weight: 600; color: #93c5fd; margin-bottom: 8px; }
.vault-form-error { color: #fca5a5; font-size: 0.78rem; margin-bottom: 6px; }
.vault-form label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.78rem;
  color: #64748b;
  margin-bottom: 8px;
}
.vault-form input {
  background: #050d1a;
  border: 1px solid #1e3a5f;
  border-radius: 4px;
  color: #e2e8f0;
  padding: 5px 8px;
  font-size: 0.82rem;
}
.vault-form-actions { display: flex; gap: 8px; margin-top: 10px; }

/* ---- Browser Task ---- */
.browser-task-screenshot { margin-top: 6px; }
.browser-screenshot-img {
  max-width: 100%;
  border-radius: 4px;
  border: 1px solid #1e3a5f;
}

.mission-action-btn.browser_task { border-color: #0891b2; background: #001a20; color: #67e8f9; }
.mission-action-btn.browser_cancel { border-color: #b91c1c; background: #200a0a; color: #fca5a5; }
```

- [ ] **Step 5: TypeScript type-check**

```
cd apps/mission-control
npx tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 6: Visual smoke-test in browser**

1. Open http://127.0.0.1:5173 in Chrome.
2. Verify VaultPanel appears collapsed in the right column. Click to expand — should show "No credentials stored yet."
3. Click "+ Add Credential" → fill Alias=`test`, Username=`myuser`, Password=`mypass` → Save.
4. Verify the credential row appears with alias + `••••••` but no password shown.
5. Delete the credential via the Delete button.
6. In chat, type: `navega a wikipedia.org y dime cuál es el artículo destacado` — LLM should generate a `browser_task` tool call button.

- [ ] **Step 7: Commit**

```
git add apps/mission-control/src/main.tsx apps/mission-control/src/styles.css
git commit -m "feat: VaultPanel UI + browser screenshot rendering in timeline"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|-----------------|------------------|
| DPAPI encrypt/decrypt + XOR fallback | Task 1 |
| SQLite `credentials` table schema | Task 1 |
| `vault_encrypt / vault_decrypt` | Task 1 |
| GET/POST/update/delete/lookup API | Task 2 |
| `/api/vault/credentials/lookup` internal-only (127.0.0.1) | Task 2 |
| `BrowserSession` dataclass | Task 3 |
| Session TTL 30 min + cleanup daemon | Task 3 |
| `_is_dangerous` with DANGER_KEYWORDS | Task 3 |
| `execute_browser_task` generator + credential injection | Task 3 |
| VLM decision via `_vlm_action` | Task 3 |
| Password fields masked in screenshots | Task 3 |
| SSE stream with step/pause_required/done/error events | Task 4 |
| `_handle_browser_task_sse` mirroring `_handle_plan_sse` | Task 4 |
| browser-confirm / browser-cancel / browser-sessions endpoints | Task 4 |
| `_resolve_vlm_model` | Task 4 |
| `browser_task` in `_AGENT_TOOL_CATALOG` (tool #4) | Task 5 |
| `_llm_tool_call_to_action` handling `browser_task` | Task 5 |
| Frontend SSE handling + screenshot rendering | Task 6 |
| Stop Browser button + confirmation dialog | Task 6 |
| `VaultPanel` — list, add, edit, delete (no password display) | Task 7 |
| CSS additions for vault + browser | Task 7 |

**Credential security invariants (verify in code):**
- `vault_list` SQL: `username_enc` and `password_enc` columns never selected — ✓ (Task 1)
- `vault_lookup` endpoint: checks `client_address.startswith("127.")` — ✓ (Task 2)
- `execute_browser_task` yields: `safe_action` strips password values in fill actions — ✓ (Task 3)
- `task_context` (contains plaintext creds) is a local variable, deleted after use — ✓ (Task 3)
- VaultForm: password inputs use `type="password"`, no "show" toggle — ✓ (Task 7)

**Placeholder scan:** No TBD/TODO in code steps. All functions are complete.

**Type consistency:** `BrowserSession.cancel` (bool), `confirm_approved` (bool), `confirm_event` (threading.Event) — used consistently in `execute_browser_task`, `confirm_action`, and `cancel_session`. `VaultCredential.has_notes` is `number` (SQLite returns 0/1) — rendered as boolean via `!!` if needed.

---

## Execution Options

Plan saved to `docs/superpowers/plans/2026-05-15-browser-agent-credential-vault.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, spec + quality review after each, no human checkpoints between tasks.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
