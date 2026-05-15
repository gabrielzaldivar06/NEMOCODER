"""
browser_agent.py — BrowserSession, TTL cleanup, VLM loop, guardrails.

Playwright is an optional dependency. If not installed, execute_browser_task
yields an error event immediately. The module still imports cleanly.
"""

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

# ---------------------------------------------------------------------------
# Optional Playwright import — module-level so tests can patch the name
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright  # type: ignore
except ImportError:
    sync_playwright = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DANGER_KEYWORDS = frozenset({
    "submit", "comprar", "pagar", "pay", "buy", "purchase", "checkout",
    "delete", "eliminar", "remove", "unsubscribe", "confirm", "proceed",
    "send money", "transfer", "sign out", "logout",
})

_SESSION_TTL = 30 * 60  # 30 minutes in seconds
_MAX_STEPS_HARD_CAP = 20


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Module-level session registry
# ---------------------------------------------------------------------------

_browser_sessions: dict[str, BrowserSession] = {}
_session_lock = threading.Lock()


# ---------------------------------------------------------------------------
# TTL cleanup daemon thread
# ---------------------------------------------------------------------------

def _cleanup_expired() -> None:
    while True:
        time.sleep(300)
        try:
            now = time.time()
            with _session_lock:
                expired = [
                    sid for sid, s in _browser_sessions.items()
                    if now - s.last_used > _SESSION_TTL
                ]
            for sid in expired:
                _close_session(sid)
        except Exception:
            pass  # Never let cleanup daemon die


threading.Thread(
    target=_cleanup_expired, daemon=True, name="browser-session-cleanup"
).start()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

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


def take_browser_frame(session_id: str) -> bytes | None:
    """Return a PNG screenshot of the current browser frame, or None if session gone."""
    with _session_lock:
        sess = _browser_sessions.get(session_id)
    if sess is None:
        return None
    try:
        mask = sess.page.locator("input[type=password]")
        return sess.page.screenshot(mask=[mask], full_page=False, timeout=5000)
    except Exception:
        return None


def get_browser_info(session_id: str) -> dict | None:
    """Return current URL, title, and step count for a session."""
    with _session_lock:
        sess = _browser_sessions.get(session_id)
    if sess is None:
        return None
    try:
        url = sess.page.url
    except Exception:
        url = sess.url
    try:
        title = sess.page.title()
    except Exception:
        title = ""
    return {
        "session_id": session_id,
        "url": url,
        "title": title,
        "step_count": sess.step_count,
        "active": True,
    }


def browser_interact(session_id: str, action: str, **kwargs) -> dict:
    """
    Send an interaction to a live browser session.

    action values: "click", "scroll", "type", "key", "navigate"
    """
    with _session_lock:
        sess = _browser_sessions.get(session_id)
    if sess is None:
        return {"ok": False, "error": "session not found"}
    try:
        if action == "click":
            x = float(kwargs.get("x", 0))
            y = float(kwargs.get("y", 0))
            sess.page.mouse.click(x, y)
        elif action == "scroll":
            delta_x = float(kwargs.get("delta_x", 0))
            delta_y = float(kwargs.get("delta_y", 0))
            sess.page.mouse.wheel(delta_x, delta_y)
        elif action == "type":
            text = str(kwargs.get("text", ""))
            sess.page.keyboard.type(text)
        elif action == "key":
            key = str(kwargs.get("key", ""))
            sess.page.keyboard.press(key)
        elif action == "navigate":
            url = str(kwargs.get("url", ""))
            sess.page.goto(url, timeout=30000)
            sess.url = url
        else:
            return {"ok": False, "error": f"unknown action: {action}"}
        sess.last_used = time.time()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _is_dangerous(action: dict) -> bool:
    """Return True if the action target or value contains a danger keyword."""
    target = str(action.get("target") or "").lower()
    value = str(action.get("value") or "").lower()
    return any(kw in target or kw in value for kw in DANGER_KEYWORDS)


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def _take_screenshot(page: object, artifacts_dir: Path, step: int) -> tuple[str, str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    fname = f"browser-step-{step:03d}-{uuid4().hex[:6]}.png"
    fpath = artifacts_dir / fname
    try:
        mask = page.locator("input[type=password]")
        page.screenshot(path=str(fpath), mask=[mask], full_page=False, timeout=15000)
    except Exception:
        fpath.write_bytes(b"")
    return str(fpath), f"/api/artifacts/image/{fname}"


# ---------------------------------------------------------------------------
# VLM action decision
# ---------------------------------------------------------------------------

def _vlm_action(
    screenshot_b64: str,
    task: str,
    history: list[dict],
    base_url: str,
    model: str,
    api_key: str = "lm-studio",
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
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    choices = data.get("choices") or []
    if not choices:
        return {"action": "done", "target": "", "value": "", "reason": "VLM returned empty choices"}
    text = choices[0].get("message", {}).get("content", "")
    # Strip thinking tags from reasoning models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<\|thinking\|>.*?<\|/thinking\|>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"action": "done", "target": "", "value": "", "reason": "VLM returned unparseable response"}


# ---------------------------------------------------------------------------
# Main execute function
# ---------------------------------------------------------------------------

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
    api_key: str = "lm-studio",
) -> Iterator[dict]:
    """
    Generator that drives a browser through a task using a VLM for decisions.

    Yields dicts with keys:
      - type: "step" | "done" | "error" | "pause_required"
    """
    max_steps = min(max_steps, _MAX_STEPS_HARD_CAP)

    # Guard: Playwright unavailable (sync_playwright is None or raises on call)
    if sync_playwright is None:
        yield {
            "type": "error",
            "message": (
                "Playwright no disponible. "
                "Instalar: pip install playwright && python -m playwright install chromium"
            ),
        }
        return

    # Resolve credentials — held in local variables ONLY, never placed in prompts
    task_cred_username: str | None = None
    task_cred_password: str | None = None
    if credential_alias and vault_db_path:
        try:
            from nemo_coding_platform.credential_vault import vault_lookup
            creds = vault_lookup(vault_db_path, credential_alias)
            task_cred_username = creds["username"]
            task_cred_password = creds["password"]
            del creds
        except KeyError:
            yield {"type": "error", "message": f"Credential alias '{credential_alias}' not found in vault"}
            return

    task_context = task
    if task_cred_username is not None:
        task_context = (
            f"{task}\n"
            "[When you encounter a username/email field, return action=fill with value=__VAULT_USERNAME__. "
            "When you encounter a password field, return action=fill with value=__VAULT_PASSWORD__. "
            "The backend substitutes the real values — never guess or invent credentials.]"
        )

    history: list[dict] = []

    # sync_playwright may have been patched with side_effect=ImportError by tests
    try:
        pw_context = sync_playwright()
    except ImportError:
        yield {
            "type": "error",
            "message": (
                "Playwright no disponible. "
                "Instalar: pip install playwright && python -m playwright install chromium"
            ),
        }
        return

    with pw_context as pw:
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
        # Register session BEFORE page.goto so cancel can interrupt navigation
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

                screenshot_path, screenshot_url = _take_screenshot(page, artifacts_dir, step)
                try:
                    screenshot_b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode()
                except OSError:
                    screenshot_b64 = ""

                page_title = page.title()

                if vlm_model and screenshot_b64:
                    try:
                        action = _vlm_action(screenshot_b64, task_context, history, base_url, vlm_model, api_key)
                    except Exception as exc:
                        action = {"action": "done", "target": "", "value": "", "reason": f"VLM error: {exc}"}
                else:
                    action = {"action": "wait", "target": "", "value": "", "reason": "no VLM — screenshot-only mode"}

                # Strip fill values from emitted events — never expose what was typed
                safe_action = dict(action)
                if action.get("action") == "fill":
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

                # Execute the action
                act = str(action.get("action") or "")
                target = str(action.get("target") or "")
                value = str(action.get("value") or "")
                # Substitute vault placeholders at execution time — credentials never enter VLM prompts
                exec_value = value
                if act == "fill":
                    if exec_value == "__VAULT_USERNAME__" and task_cred_username is not None:
                        exec_value = task_cred_username
                    elif exec_value == "__VAULT_PASSWORD__" and task_cred_password is not None:
                        exec_value = task_cred_password
                try:
                    if act == "click":
                        page.click(target, timeout=30000)
                    elif act == "fill":
                        page.fill(target, exec_value, timeout=30000)
                    elif act == "navigate":
                        page.goto(target, timeout=30000)
                    elif act == "scroll":
                        page.evaluate("window.scrollBy(0, 400)")
                    elif act == "wait":
                        time.sleep(2)
                    history.append({
                        "step": step, "action": act, "target": target,
                        "reason": str(action.get("reason") or ""),
                    })
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
            for obj in (page, ctx, browser):
                try:
                    obj.close()
                except Exception:
                    pass
            with _session_lock:
                _browser_sessions.pop(session_id, None)
