# Browser Agent + Credential Vault — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add autonomous web browsing capability to the Space Code agent, with a DPAPI-encrypted credential vault so the agent can log into sites and navigate on behalf of the user.

**Architecture:** Two new backend modules (`credential_vault.py`, `browser_agent.py`) plus one new API surface and frontend additions to the Artifact Studio and telemetry panel. The vault stores credentials encrypted with Windows DPAPI; the browser agent uses Playwright headless + an on-device VLM to autonomously navigate and interact with web pages, streaming screenshots to the Artifact Studio via SSE.

**Tech Stack:** Python `win32crypt` (DPAPI), SQLite, Playwright `sync_api`, existing LM Studio VLM (gemma-4-e4b-it or equivalent), React/TypeScript frontend, existing SSE infrastructure pattern.

---

## Scope

Two tightly coupled but independently testable subsystems:

1. **Credential Vault** — encrypted local storage of named credentials, CRUD API, vault UI panel.
2. **Browser Agent** — autonomous Playwright loop driven by VLM visual reasoning, SSE live screenshots, guardrails, credential injection.

---

## Module 1 — Credential Vault (`credential_vault.py`)

### Storage

SQLite database at `.spacecode-runtimes/mission-control/vault.db`.

```sql
CREATE TABLE credentials (
    id          TEXT PRIMARY KEY,        -- uuid4 hex
    alias       TEXT NOT NULL UNIQUE,    -- e.g. "github", "gmail"
    username_enc BLOB NOT NULL,          -- DPAPI-encrypted bytes
    password_enc BLOB NOT NULL,          -- DPAPI-encrypted bytes
    url_pattern TEXT,                    -- optional, e.g. "github.com"
    notes_enc   BLOB,                    -- DPAPI-encrypted bytes or NULL
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

### Encryption

- **`vault_encrypt(plaintext: str) -> bytes`** — `win32crypt.CryptProtectData(plaintext.encode(), None, None, None, None, 0)` → returns DPAPI blob. Bound to the current Windows user session automatically.
- **`vault_decrypt(blob: bytes) -> str`** — `win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode()`.
- Fallback for non-Windows: XOR obfuscation with machine hostname as key + warning logged.

### API Endpoints

```
GET    /api/vault/credentials
       → [{ id, alias, url_pattern, has_notes, created_at, updated_at }]
       (never returns username/password)

POST   /api/vault/credentials
       body: { alias, username, password, url_pattern?, notes? }
       → { id, alias, created_at }

PUT    /api/vault/credentials/{id}
       body: { alias?, username?, password?, url_pattern?, notes? }
       → { id, alias, updated_at }

DELETE /api/vault/credentials/{id}
       → { ok: true }

POST   /api/vault/credentials/lookup   [INTERNAL — not callable from browser JS]
       body: { alias }
       → { username, password }   (decrypted, in-process only — used by browser_agent)
```

The lookup endpoint is internal: it only accepts requests from `127.0.0.1` and is blocked by a `_require_internal` check.

---

## Module 2 — Browser Agent (`browser_agent.py`)

### Session Management

```python
@dataclass
class BrowserSession:
    session_id: str
    page: playwright.sync_api.Page
    browser: playwright.sync_api.Browser
    context: playwright.sync_api.BrowserContext
    step_count: int
    cancel: bool
    confirm_event: threading.Event   # set when user confirms a pause
    confirm_approved: bool
    last_used: float                 # time.time()

_browser_sessions: dict[str, BrowserSession] = {}
_session_lock = threading.Lock()
```

Session TTL: 30 minutes of inactivity. A background daemon thread cleans up expired sessions every 5 minutes.

### Autonomous Loop

```python
def execute_browser_task(
    session_id: str,
    url: str,
    task: str,
    credential_alias: str | None,
    max_steps: int,
    config: ServerConfig,
) -> Iterator[dict]:
```

**Steps:**
1. Create or reuse `BrowserSession` with `playwright.chromium.launch(headless=True)`.
2. If `credential_alias` provided: lookup from vault → inject into `task` context string (never into SSE events or logs).
3. Navigate to `url`.
4. Loop up to `max_steps`:
   a. Take screenshot → save to artifacts dir → yield `step` event with `screenshot_url`.
   b. Call VLM with screenshot + task + history → parse `BrowserAction` JSON.
   c. If `action.kind == "done"`: break.
   d. If `_is_dangerous(action)`: yield `pause_required` → block on `confirm_event` → if not approved: break.
   e. Execute Playwright action.
5. Yield `done` event.

### VLM Decision

Uses `_lmstudio_vision_call(screenshot_b64, prompt)` — new helper that sends image + text to the VLM endpoint (`/v1/chat/completions` with `content: [{type:"image_url",...}, {type:"text",...}]`).

**Prompt template:**
```
You are a web navigation agent. Your task: {task}
Steps taken so far: {json(history[-5:])}
Look at the screenshot and respond ONLY with valid JSON (no explanation):
{"action": "fill|click|navigate|scroll|wait|done",
 "target": "CSS selector OR visible text to match",
 "value": "only for fill action",
 "reason": "one line"}
Action "done" means the task is complete.
```

**VLM fallback:** if no VLM available (`_resolve_vlm_model()` returns None), the loop still runs but only takes screenshots and yields `step` events — the user guides each step manually via the frontend.

### Guardrails

**Dangerous action detection** — `_is_dangerous(action) -> bool`:
```python
DANGER_KEYWORDS = {
    "submit", "comprar", "pagar", "pay", "buy", "purchase", "checkout",
    "delete", "eliminar", "remove", "unsubscribe", "confirm", "proceed",
    "send money", "transfer", "sign out", "logout"
}
```
Checks if any keyword appears in `action.target.lower()` or `action.value.lower()`.

**Hard limits:**
- `max_steps` capped at 20 in backend regardless of what's passed.
- Each Playwright action has a 30s timeout.
- Session TTL 30 minutes.
- Credentials never appear in SSE events, logs, NEMO memory, or screenshots (Playwright does not screenshot password fields when `mask_ids` is set).

### New API Endpoints

```
POST /api/agent/browser-task
     body: { url, task, credential_alias?, max_steps?, session_id? }
     → SSE stream:
       event: step           { step, action_taken, screenshot_url, page_title }
       event: pause_required { step, action, reason, session_id }
       event: done           { steps_taken, success, final_screenshot_url, session_id }
       event: error          { message }

POST /api/agent/browser-confirm
     body: { session_id, approved: bool }
     → { ok: true }

POST /api/agent/browser-cancel
     body: { session_id }
     → { ok: true }

GET  /api/agent/browser-sessions
     → [{ session_id, step_count, last_used, url }]
```

### New Agent Tool

Added to `_AGENT_TOOL_CATALOG`:

```
4. browser_task — autonomous web navigation with visual AI
   {"tool": "browser_task", "params": {
     "url": "https://...",
     "task": "full description of what to accomplish",
     "credential_alias": "github",
     "max_steps": 8
   }}
   Use when the user asks to navigate, log in, fill forms, or interact with websites.
   Credentials are resolved from the vault by alias — never put passwords in params.
```

---

## Frontend Changes

### New `AgentAction` kind

```typescript
type AgentActionKind = ... | "browser_task"
```

### `runAgentAction` — browser_task branch

1. Open SSE to `POST /api/agent/browser-task` with action payload.
2. On `step` event: update/create artifact of type `browser_screenshot` in Artifact Studio with `screenshot_url`. Replace previous step's artifact or add to strip.
3. On `pause_required`: show confirmation dialog with action description. On confirm → `POST /api/agent/browser-confirm { approved: true }`. On cancel → `{ approved: false }`.
4. On `done`: inject "Browser task complete" message into timeline with final screenshot artifact. Clean up stop button.
5. Inject "Stop Browser" button (red) into the message that triggered the task. On click → `POST /api/agent/browser-cancel`.

### Vault UI Panel

New collapsible section in the right column (below NEMO ORBIT COPY) or as a sub-tab in the CORE panel.

**Components:**
- `VaultPanel` — lists credentials as rows: `alias`, `url_pattern`, `••••••` (masked), Edit/Delete buttons.
- `VaultForm` — add/edit form: Alias (required), Username, Password (type=password), URL Pattern (optional), Notes (optional). Never pre-fills password on edit.
- No "show password" toggle — passwords only enter and leave via the form, never displayed.

### CSS additions

```css
.vault-panel { ... }
.vault-credential-row { ... }
.vault-credential-alias { font-weight: bold; }
.vault-credential-mask { opacity: 0.5; }
.browser-task-strip { display: flex; gap: 4px; overflow-x: auto; }
.browser-task-confirm-dialog { ... }
.mission-action-btn.browser_task { border-color: #0891b2; background: #001a20; color: #67e8f9; }
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Playwright not installed | SSE `error` event with install instructions |
| VLM not available | Fallback to screenshot-only mode, user guides manually |
| DPAPI unavailable (non-Windows) | XOR obfuscation fallback + warning in vault UI |
| Session not found | 404 JSON response |
| `max_steps` reached | `done` event with `success: false`, `reason: "max_steps_reached"` |
| User cancels at pause | `done` event with `success: false`, `reason: "user_rejected_action"` |
| Playwright timeout on action | SSE `step` event with `error: true`, loop continues to next step |

---

## Security Notes

- Vault credentials are never returned in GET responses — only aliases and metadata.
- `/api/vault/credentials/lookup` is internal-only (127.0.0.1 check).
- Credentials resolved from vault are held in a local variable for the duration of the browser session, then garbage collected. Never written to disk, logs, or NEMO.
- Screenshots are saved to the artifacts dir as PNG files; Playwright's `mask` option is used for any `input[type=password]` elements.
- The `browser_task` SSE stream never includes credential values — only `credential_alias` (the alias name).

---

## File Structure

| File | Action |
|------|--------|
| `src/nemo_coding_platform/credential_vault.py` | New — vault CRUD + DPAPI |
| `src/nemo_coding_platform/browser_agent.py` | New — autonomous browser loop |
| `src/nemo_coding_platform/mission_control_server.py` | Modify — new routes, tool catalog, VLM helper |
| `apps/mission-control/src/main.tsx` | Modify — AgentAction kind, runAgentAction, VaultPanel |
| `apps/mission-control/src/styles.css` | Modify — vault + browser CSS |
| `tests/test_credential_vault.py` | New — vault unit tests |
| `tests/test_browser_agent.py` | New — browser agent unit tests (mocked Playwright) |
