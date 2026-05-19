# Pollinations Media Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `generate_image`, `generate_audio`, `generate_video`, and `generate_text` as backend-executed tools the active LLM (local or remote) can invoke in parallel from chat and from the plan loop.

**Architecture:** All changes are in `mission_control_server.py`. Four HTTP helpers call Pollinations REST endpoints, a dispatcher runs them in parallel via `ThreadPoolExecutor`, results are appended to the chat response as an annotation. The same helpers are wired into `api_agent_plan_gen` so the plan loop can call them mid-iteration. No new endpoints, no frontend changes.

**Tech Stack:** Python 3.11+, `urllib.request` (already in use), `concurrent.futures.ThreadPoolExecutor` (already imported), `uuid4` (already imported).

---

## File Structure

- **Modify:** `src/nemo_coding_platform/mission_control_server.py`
  - Add `POLLINATIONS_TOOLS` constant (module-level, before `_AGENT_TOOL_CATALOG` ~line 5800)
  - Add `_pollinations_image`, `_pollinations_audio`, `_pollinations_video`, `_pollinations_text` (same location)
  - Add `_execute_pollinations_tool` dispatcher (same location)
  - Extend `_AGENT_TOOL_CATALOG` string (line 5802)
  - Extend `_AGENT_TOOL_SCHEMAS` list (line 5836)
  - Extend `api_agent_message` (before line 7652 — the `_parse_llm_tool_calls` block)
  - Extend `api_agent_plan_gen` (around line 6723 — the `critique_user` construction)

- **Create:** `tests/test_pollinations_tools.py`

---

### Task 1: Pollinations HTTP helpers and dispatcher

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`
- Create: `tests/test_pollinations_tools.py`

Context: `urllib.request`, `json`, `uuid4`, `Path`, `time` are all already imported. `ThreadPoolExecutor` and `as_completed` are at line 18. Insert new code right after line 5800 (blank line after `_extract_code_block`) and before `_AGENT_TOOL_CATALOG`.

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pollinations_tools.py`:

```python
"""Tests for Pollinations media tool helpers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nemo_coding_platform.mission_control_server as mcs
from nemo_coding_platform.mission_control_server import MissionControlServerConfig


def _make_config(tmp: str) -> MissionControlServerConfig:
    root = Path(tmp)
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    return MissionControlServerConfig.from_paths(
        repo, root / "runtimes", root / "apply", memory_db=None
    )


def _mock_urlopen(content: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_resp.headers.get.return_value = "application/octet-stream"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestPollinationsImage(unittest.TestCase):
    def test_saves_artifact_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff")):
                result = mcs._pollinations_image({"prompt": "a sunset"}, config)
        self.assertIn("artifact_path", result)
        self.assertEqual(result["model_used"], "flux")
        self.assertTrue(Path(result["artifact_path"]).exists())

    def test_custom_model_and_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff")) as mock_open:
                mcs._pollinations_image({"prompt": "city", "model": "flux-realism", "width": 512, "height": 512}, config)
            call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("flux-realism", call_url)
        self.assertIn("width=512", call_url)

    def test_missing_prompt_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_image({}, config)
        self.assertIn("error", result)

    def test_network_error_returns_error_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                result = mcs._pollinations_image({"prompt": "test"}, config)
        self.assertIn("error", result)


class TestPollinationsAudio(unittest.TestCase):
    def test_saves_mp3_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"ID3fake")):
                result = mcs._pollinations_audio({"text": "hello world"}, config)
        self.assertIn("artifact_path", result)
        self.assertEqual(result["voice_used"], "nova")
        self.assertTrue(result["artifact_path"].endswith(".mp3"))

    def test_custom_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"ID3fake")) as mock_open:
                mcs._pollinations_audio({"text": "hi", "voice": "heart"}, config)
            call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("voice=heart", call_url)

    def test_missing_text_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_audio({}, config)
        self.assertIn("error", result)


class TestPollinationsText(unittest.TestCase):
    def test_returns_text_inline(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"Once upon a time")):
            result = mcs._pollinations_text({"prompt": "tell me a story"})
        self.assertIn("text", result)
        self.assertEqual(result["text"], "Once upon a time")
        self.assertEqual(result["model_used"], "openai")

    def test_custom_model(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"code here")) as mock_open:
            mcs._pollinations_text({"prompt": "write a sort", "model": "qwen-coder"})
        call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("qwen-coder", call_url)

    def test_missing_prompt_returns_error(self):
        result = mcs._pollinations_text({})
        self.assertIn("error", result)

    def test_does_not_save_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"result")):
                result = mcs._pollinations_text({"prompt": "test"})
            artifacts = list((Path(tmp) / "runtimes").rglob("*"))
        self.assertNotIn("artifact_path", result)
        self.assertEqual(artifacts, [])


class TestExecutePollinationsTool(unittest.TestCase):
    def test_dispatches_to_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/path.jpg"}) as mock_img:
                result = mcs._execute_pollinations_tool(
                    {"tool": "generate_image", "params": {"prompt": "test"}}, config
                )
        mock_img.assert_called_once()
        self.assertEqual(result["artifact_path"], "/fake/path.jpg")

    def test_dispatches_to_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_audio",
                       return_value={"artifact_path": "/fake/audio.mp3"}) as mock_aud:
                mcs._execute_pollinations_tool(
                    {"tool": "generate_audio", "params": {"text": "hello"}}, config
                )
        mock_aud.assert_called_once()

    def test_dispatches_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_text",
                       return_value={"text": "result"}) as mock_txt:
                mcs._execute_pollinations_tool(
                    {"tool": "generate_text", "params": {"prompt": "task"}}, config
                )
        mock_txt.assert_called_once()

    def test_unknown_tool_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._execute_pollinations_tool({"tool": "unknown_tool", "params": {}}, config)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to confirm they fail**

```powershell
cd c:\dev\dev4
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py -v 2>&1 | head -30
```

Expected: `AttributeError: module ... has no attribute '_pollinations_image'`

- [ ] **Step 3: Add POLLINATIONS_TOOLS constant and helper functions**

Find the blank line at line 5800 in `mission_control_server.py` (after `_extract_code_block` ends, before `_AGENT_TOOL_CATALOG`). Insert the following block:

```python
# ── Pollinations media tools ──────────────────────────────────────────────────

POLLINATIONS_TOOLS: frozenset[str] = frozenset(
    {"generate_image", "generate_audio", "generate_video", "generate_text"}
)


def _pollinations_image(params: dict[str, object], config: "MissionControlServerConfig") -> dict[str, object]:
    prompt = str(params.get("prompt") or "")
    if not prompt:
        return {"error": "prompt is required"}
    model = str(params.get("model") or "flux")
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 1024)
    enhance = "true" if params.get("enhance", True) else "false"
    encoded = urllib.parse.quote(prompt, safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?model={model}&width={width}&height={height}&enhance={enhance}"
    )
    if params.get("seed") is not None:
        url += f"&seed={int(params['seed'])}"  # type: ignore[arg-type]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SpaceCode/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            image_bytes = resp.read()
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "images"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dest = artifacts_dir / f"pollinations-{uuid4().hex[:8]}.jpg"
        dest.write_bytes(image_bytes)
        return {"artifact_path": str(dest), "url": url, "model_used": model}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _pollinations_audio(params: dict[str, object], config: "MissionControlServerConfig") -> dict[str, object]:
    text = str(params.get("text") or "")
    if not text:
        return {"error": "text is required"}
    voice = str(params.get("voice") or "nova")
    model = str(params.get("model") or "openai-audio")
    encoded = urllib.parse.quote(text, safe="")
    url = f"https://audio.pollinations.ai/{encoded}?voice={voice}&model={model}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SpaceCode/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio_bytes = resp.read()
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "audio"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dest = artifacts_dir / f"pollinations-{uuid4().hex[:8]}.mp3"
        dest.write_bytes(audio_bytes)
        return {"artifact_path": str(dest), "url": url, "voice_used": voice}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _pollinations_video(params: dict[str, object], config: "MissionControlServerConfig") -> dict[str, object]:
    prompt = str(params.get("prompt") or "")
    if not prompt:
        return {"error": "prompt is required"}
    model = str(params.get("model") or "seedance-1-lite")
    duration = int(params.get("duration") or 5)
    body: dict[str, object] = {"prompt": prompt, "model": model, "duration": duration}
    if params.get("keyframe_image"):
        body["image"] = str(params["keyframe_image"])
    post_data = json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://video.pollinations.ai/",
            data=post_data,
            headers={"Content-Type": "application/json", "User-Agent": "SpaceCode/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            init_result = json.loads(resp.read().decode("utf-8"))
        video_url = str(init_result.get("url") or init_result.get("video_url") or "")
        if not video_url:
            return {"error": f"no video URL in response: {str(init_result)[:200]}"}
        # Poll until the video is ready (max 120s, 5s interval)
        for _ in range(24):
            time.sleep(5)
            poll_req = urllib.request.Request(video_url, headers={"User-Agent": "SpaceCode/1.0"})
            with urllib.request.urlopen(poll_req, timeout=30) as poll_resp:
                content_type = poll_resp.headers.get("Content-Type", "")
                raw = poll_resp.read()
            if "video" in content_type or "mp4" in content_type:
                artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "video"
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                dest = artifacts_dir / f"pollinations-{uuid4().hex[:8]}.mp4"
                dest.write_bytes(raw)
                return {"artifact_path": str(dest), "url": video_url, "model_used": model}
            try:
                poll_data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if poll_data.get("status") == "done":
                final_url = str(poll_data.get("url") or video_url)
                dl_req = urllib.request.Request(final_url, headers={"User-Agent": "SpaceCode/1.0"})
                with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
                    video_bytes = dl_resp.read()
                artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "video"
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                dest = artifacts_dir / f"pollinations-{uuid4().hex[:8]}.mp4"
                dest.write_bytes(video_bytes)
                return {"artifact_path": str(dest), "url": final_url, "model_used": model}
        return {"error": "video generation timed out after 120s"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _pollinations_text(params: dict[str, object]) -> dict[str, object]:
    prompt = str(params.get("prompt") or "")
    if not prompt:
        return {"error": "prompt is required"}
    model = str(params.get("model") or "openai")
    encoded = urllib.parse.quote(prompt, safe="")
    url = f"https://text.pollinations.ai/{encoded}?model={model}"
    if params.get("system"):
        url += f"&system={urllib.parse.quote(str(params['system']), safe='')}"
    if params.get("seed") is not None:
        url += f"&seed={int(params['seed'])}"  # type: ignore[arg-type]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SpaceCode/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text_result = resp.read().decode("utf-8")
        return {"text": text_result, "model_used": model}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _execute_pollinations_tool(
    inv: dict[str, object], config: "MissionControlServerConfig"
) -> dict[str, object]:
    tool = str(inv.get("tool") or "")
    params: dict[str, object] = dict(inv.get("params") or {})
    if tool == "generate_image":
        return _pollinations_image(params, config)
    if tool == "generate_audio":
        return _pollinations_audio(params, config)
    if tool == "generate_video":
        return _pollinations_video(params, config)
    if tool == "generate_text":
        return _pollinations_text(params)
    return {"error": f"unknown pollinations tool: {tool}"}
```

- [ ] **Step 4: Verify `urllib.parse` is imported**

```powershell
Select-String -Path src\nemo_coding_platform\mission_control_server.py -Pattern "import urllib.parse" | Select-Object -First 3
```

Expected: at least one line with `import urllib.parse`. If missing, add `import urllib.parse` near the other `urllib` imports at the top of the file.

- [ ] **Step 5: Run tests — expect most to pass**

```powershell
cd c:\dev\dev4
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py -v
```

Expected: all tests pass (helpers are pure functions with mocked HTTP).

- [ ] **Step 6: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_pollinations_tools.py
git commit -m "feat: add Pollinations media tool helpers (image/audio/video/text)"
```

---

### Task 2: Extend tool catalog and schemas

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

Context: `_AGENT_TOOL_CATALOG` is the string at ~line 5802 injected as a system message. It currently lists 5 tools. `_AGENT_TOOL_SCHEMAS` is the OpenAI function calling list at ~line 5836. Both need 4 new entries. Update the header count from 5 to 9.

---

- [ ] **Step 1: Write failing catalog tests**

Add this class to `tests/test_pollinations_tools.py`:

```python
class TestPollinationsToolCatalog(unittest.TestCase):
    def test_catalog_contains_all_four_tool_names(self):
        for tool in ("generate_image", "generate_audio", "generate_video", "generate_text"):
            self.assertIn(tool, mcs._AGENT_TOOL_CATALOG, f"Tool '{tool}' not in catalog")

    def test_schemas_contain_all_four_tool_names(self):
        schema_names = {s["function"]["name"] for s in mcs._AGENT_TOOL_SCHEMAS}
        for tool in ("generate_image", "generate_audio", "generate_video", "generate_text"):
            self.assertIn(tool, schema_names, f"Tool '{tool}' not in schemas")

    def test_pollinations_tools_constant_has_all_four(self):
        self.assertEqual(
            mcs.POLLINATIONS_TOOLS,
            frozenset({"generate_image", "generate_audio", "generate_video", "generate_text"}),
        )
```

Run to confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py::TestPollinationsToolCatalog -v
```

Expected: FAIL — `generate_image not in _AGENT_TOOL_CATALOG`

- [ ] **Step 2: Update `_AGENT_TOOL_CATALOG`**

Find this line in `mission_control_server.py`:
```
AGENT ACTION TOOLS — THE ONLY 5 TOOLS YOU CAN TRIGGER
```

Replace it with:
```
AGENT ACTION TOOLS — THE ONLY 9 TOOLS YOU CAN TRIGGER
```

Then find the line that starts `CRITICAL: These 5 are the ONLY tools` and replace with:
```
CRITICAL: These 9 are the ONLY tools available to you. Do NOT embed NEMO tool names in your response.
```

Then find the closing `"""` of `_AGENT_TOOL_CATALOG` and insert before it:

```
6. generate_image — generate a static image via Pollinations AI
   Use when the user asks to draw, visualize, create an image, or generate artwork.
   params: prompt (required), model (default "flux", options: flux|flux-realism|flux-anime|gpt-image-1|seedream-3|kontext),
           width (default 1024), height (default 1024), seed (optional), enhance (default true)

7. generate_audio — synthesize speech or voice audio via Pollinations AI
   Use when the user asks to narrate text, create audio, or generate a voiceover.
   params: text (required), voice (default "nova", options: alloy|echo|fable|onyx|nova|shimmer|heart|aria|adam|bill|brian),
           model (default "openai-audio")

8. generate_video — generate a short video clip via Pollinations AI
   Use when the user asks to animate, create a video, or produce a clip.
   params: prompt (required), model (default "seedance-1-lite", options: seedance-1-lite|wan-fast|veo-2),
           duration (default 5, seconds), keyframe_image (optional — path to an image artifact for the first frame)

9. generate_text — delegate a text subtask to a Pollinations text model (sub-agent pattern)
   Use when you need a specific text generation done by a different model (translation, code, creative writing).
   params: prompt (required), model (default "openai", options: openai|qwen-coder|deepseek|mistral|claude-hybridspace|gemini-2.0),
           system (optional — system instruction for the sub-agent), seed (optional)
```

- [ ] **Step 3: Extend `_AGENT_TOOL_SCHEMAS`**

Find the closing `]` of `_AGENT_TOOL_SCHEMAS` and insert before it:

```python
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate a static image via Pollinations AI. Use when the user asks to draw, visualize, create an image, or generate artwork.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed image description"},
                    "model": {"type": "string", "description": "Model: flux (default), flux-realism, flux-anime, gpt-image-1, seedream-3, kontext"},
                    "width": {"type": "integer", "description": "Width in pixels (default 1024)"},
                    "height": {"type": "integer", "description": "Height in pixels (default 1024)"},
                    "seed": {"type": "integer", "description": "Optional random seed for reproducibility"},
                    "enhance": {"type": "boolean", "description": "Pollinations prompt enhancement (default true)"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_audio",
            "description": "Synthesize speech via Pollinations AI. Use when the user asks to narrate text, create a voiceover, or generate audio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to synthesize"},
                    "voice": {"type": "string", "description": "Voice: nova (default), alloy, echo, fable, onyx, shimmer, heart, aria, adam, bill, brian"},
                    "model": {"type": "string", "description": "TTS model (default: openai-audio)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a short video clip via Pollinations AI. Use when the user asks to animate, create a video, or produce a clip.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Video description"},
                    "model": {"type": "string", "description": "Model: seedance-1-lite (default), wan-fast, veo-2"},
                    "duration": {"type": "integer", "description": "Duration in seconds (default 5)"},
                    "keyframe_image": {"type": "string", "description": "Optional path to an existing image artifact to use as first frame"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_text",
            "description": "Delegate a text subtask to a Pollinations text model. Use to generate text with a different model (translation, creative writing, code).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Task or prompt for the text sub-agent"},
                    "model": {"type": "string", "description": "Model: openai (default), qwen-coder, deepseek, mistral, claude-hybridspace, gemini-2.0"},
                    "system": {"type": "string", "description": "Optional system instruction for the sub-agent"},
                    "seed": {"type": "integer", "description": "Optional random seed"},
                },
                "required": ["prompt"],
            },
        },
    },
```

- [ ] **Step 4: Run catalog tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py::TestPollinationsToolCatalog -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_pollinations_tools.py
git commit -m "feat: add Pollinations tools to _AGENT_TOOL_CATALOG and _AGENT_TOOL_SCHEMAS"
```

---

### Task 3: Wire Pollinations execution into `api_agent_message`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

Context: In `api_agent_message`, after the model response is obtained (line ~7651), the code calls `_parse_llm_tool_calls(response)` to extract AgentActions. Insert Pollinations detection + parallel execution + response annotation **before** that block (before line 7652). The `config` variable is already available in `api_agent_message` as a parameter. The annotation appended to `response` shows up in the chat as the model's final message.

---

- [ ] **Step 1: Write failing integration test**

Add this class to `tests/test_pollinations_tools.py`:

```python
class TestApiAgentMessagePollinationsIntegration(unittest.TestCase):
    def _make_config_and_payload(self, tmp: str) -> tuple:
        config = _make_config(tmp)
        payload = {
            "message": "generate an image of a sunset",
            "model_base_url": "http://localhost:1234/v1",
            "history": [],
            "nemo_mcp_url": "",
        }
        return config, payload

    def test_pollinations_image_result_appended_to_response(self):
        """When model response contains generate_image JSON, result annotation appears in output."""
        fake_model_response = '{"tool": "generate_image", "params": {"prompt": "a sunset over the ocean"}}'
        with tempfile.TemporaryDirectory() as tmp:
            config, payload = self._make_config_and_payload(tmp)
            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion",
                       return_value=fake_model_response), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call", return_value={"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}), \
                 patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/sunset.jpg", "url": "https://img", "model_used": "flux"}):
                result = mcs.api_agent_message(config, payload, server=None)
        response_text = result.get("response", "")
        self.assertIn("sunset.jpg", response_text)
        self.assertIn("[Media artifacts generated]", response_text)

    def test_no_pollinations_call_leaves_response_unchanged(self):
        """When model response has no Pollinations tool call, no annotation is added."""
        fake_model_response = "Here is a poem about the ocean."
        with tempfile.TemporaryDirectory() as tmp:
            config, payload = self._make_config_and_payload(tmp)
            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion",
                       return_value=fake_model_response), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call", return_value={"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}):
                result = mcs.api_agent_message(config, payload, server=None)
        response_text = result.get("response", "")
        self.assertNotIn("[Media artifacts generated]", response_text)
```

Run to confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py::TestApiAgentMessagePollinationsIntegration -v
```

Expected: FAIL — annotation not in response.

- [ ] **Step 2: Insert Pollinations block in `api_agent_message`**

Find this comment in `api_agent_message` (line ~7652):

```python
    # Parse model tool calls FIRST so we know if it already routed correctly.
    # If the model emitted plan_generate/handoff_start/etc., skip the artifact fallback.
    _pre_actions: list[AgentAction] = []
```

Insert the following block **immediately before** that comment:

```python
    # --- Pollinations media tools: detect, execute in parallel, annotate response ---
    _media_invocations = [
        inv for inv in _parse_llm_tool_calls(response)
        if str(inv.get("tool")) in POLLINATIONS_TOOLS
    ]
    if _media_invocations:
        _media_results: dict[str, dict[str, object]] = {}
        with ThreadPoolExecutor(max_workers=len(_media_invocations)) as _media_pool:
            _media_futures = {
                _media_pool.submit(_execute_pollinations_tool, inv, config): inv
                for inv in _media_invocations
            }
            for _media_fut in as_completed(_media_futures):
                _media_inv = _media_futures[_media_fut]
                _media_results[str(_media_inv.get("tool"))] = _media_fut.result()
        _annotation_lines: list[str] = []
        for inv in _media_invocations:
            tool_name = str(inv.get("tool"))
            res = _media_results.get(tool_name, {})
            if "error" in res:
                _annotation_lines.append(f"- {tool_name}: ERROR — {res['error']}")
            elif "artifact_path" in res:
                _annotation_lines.append(f"- {tool_name}: saved to `{res['artifact_path']}`")
            elif "text" in res:
                _annotation_lines.append(f"- {tool_name} (sub-agent): {str(res['text'])[:200]}")
        if _annotation_lines:
            response += "\n\n[Media artifacts generated]\n" + "\n".join(_annotation_lines)
```

- [ ] **Step 3: Run integration tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py::TestApiAgentMessagePollinationsIntegration -v
```

Expected: both tests pass.

- [ ] **Step 4: Run full test suite to check for regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py tests/test_plan_loop_refinement.py tests/test_mission_control_server.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_pollinations_tools.py
git commit -m "feat: wire Pollinations tool execution into api_agent_message"
```

---

### Task 4: Wire Pollinations execution into `api_agent_plan_gen`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

Context: In `api_agent_plan_gen`, after the code generation step, the critique prompt is built at line ~6723:
```python
critique_user = (
    f"Task: {objective[:200]}\n{exec_note}\n\n"
    f"Code{_score_ctx}:\n{_critique_target[:1500]}"
)
```
We detect Pollinations tool calls in the raw generation response (the text around the code block, not the code itself), execute them, and append artifact paths to `critique_user`. The `config` variable is already available in `api_agent_plan_gen`.

---

- [ ] **Step 1: Confirm the generation response variable name**

The raw LLM response in the plan loop is stored in `code_response` (set after `_extract_code_block` picks the best candidate from `responses`). Grep to confirm:

```powershell
Select-String -Path src\nemo_coding_platform\mission_control_server.py -Pattern "code_response\s*=" | Select-Object LineNumber, Line | Format-Table -AutoSize
```

Expected: lines inside `api_agent_plan_gen` showing `code_response = resp` and `code_response = longest`.

- [ ] **Step 2: Write failing plan loop test**

Add this class to `tests/test_pollinations_tools.py`:

```python
class TestPlanLoopPollinationsIntegration(unittest.TestCase):
    def _make_config(self, tmp: str) -> "MissionControlServerConfig":
        return _make_config(tmp)

    def _fake_nemo(self, config, tool_calls, tool_name, **kw):
        return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}

    def test_generate_image_artifact_path_in_critique_prompt(self):
        """When gen_raw contains generate_image, the artifact path appears in the critique."""
        captured_critique_users = []

        def _lm_side(payload, system, user, **kw):
            if "evaluator" in system.lower():
                captured_critique_users.append(user)
                return '{"score":7,"present":[],"missing":[],"improvements":[],"summary":"ok"}'
            return '{"tool": "generate_image", "params": {"prompt": "chart"}}\nprint("hello")'

        with tempfile.TemporaryDirectory() as tmp:
            config = self._make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._plan_lm_call",
                       side_effect=_lm_side), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       side_effect=self._fake_nemo), \
                 patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/chart.jpg", "url": "https://img", "model_used": "flux"}), \
                 patch("time.sleep"):
                list(mcs.api_agent_plan_gen(config, {
                    "objective": "print hello",
                    "max_iterations": 1,
                    "quality_threshold": 99.0,
                    "model_base_url": "http://localhost:1234/v1",
                }))
        self.assertTrue(any("chart.jpg" in u for u in captured_critique_users),
                        f"artifact path not found in critique prompts: {captured_critique_users}")
```

Run to confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest "tests/test_pollinations_tools.py::TestPlanLoopPollinationsIntegration" -v
```

Expected: FAIL — `chart.jpg` not found in critique prompt.

- [ ] **Step 3: Insert Pollinations block before critique_user construction**

Find this block in `api_agent_plan_gen` (line ~6713):

```python
        exec_note = "Execution: OK" if exec_ok else f"Execution FAILED: {exec_output[:300]}"
        if harness_result and not harness_result.get("skipped"):
            exec_note += f" | Tests: {harness_result.get('passed',0)} passed, {harness_result.get('failed',0)} failed"
        # Build mode-specific critique prompt based on current best score
        critique_sys = _build_critique_sys(best_score)
        # Critique the best artifact, not the (possibly regressed) current one.
        # This ensures improvement feedback is always anchored to the highest-quality version.
        _critique_target = best_code if best_code else code
        _score_ctx = f" (previous best: {best_score:.1f}/10)" if best_score > 0 else ""
        critique_user = (
            f"Task: {objective[:200]}\n{exec_note}\n\n"
            f"Code{_score_ctx}:\n{_critique_target[:1500]}"
        )
```

Replace the `critique_user = (...)` assignment with:

```python
        # Detect and execute any Pollinations tool calls emitted alongside the code
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
```

- [ ] **Step 4: Verify insertion point**

The Pollinations block from Step 3 goes after the fix-retry logic (~line 6668) and before the `exec_note =` line (~line 6713). Both `code_response` and `config` are in scope at that location.

- [ ] **Step 5: Run plan loop test**

```powershell
.\.venv\Scripts\python.exe -m pytest "tests/test_pollinations_tools.py::TestPlanLoopPollinationsIntegration" -v
```

Expected: PASS.

- [ ] **Step 6: Run the full test_pollinations_tools.py suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pollinations_tools.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Run broader regression check**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_plan_loop_refinement.py tests/test_pollinations_tools.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_pollinations_tools.py
git commit -m "feat: wire Pollinations tool execution into api_agent_plan_gen critique loop"
```

---

### Task 5: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Restart the backend**

```powershell
.\scripts\start-mvp-local.ps1
```

Wait for `Backend ready` message.

- [ ] **Step 2: Verify generate_image from chat**

Open `http://127.0.0.1:5173` in Chrome. In the chat, type:

```
genera una imagen de una ciudad futurista al atardecer
```

Expected behavior:
1. LLM responds with `{"tool": "generate_image", "params": {"prompt": "..."}}` in its text
2. Backend executes Pollinations HTTP call (~5-10s)
3. Response appears with `[Media artifacts generated]` annotation showing the artifact path
4. File exists at `.spacecode-runtimes/mission-control/artifacts/images/pollinations-*.jpg`

- [ ] **Step 3: Verify generate_audio from chat**

```
narrate this text with voice "nova": "Welcome to Mission Control, your AI-powered development environment."
```

Expected: response includes `artifacts/audio/pollinations-*.mp3` path.

- [ ] **Step 4: Verify generate_text sub-agent from chat**

```
usa generate_text con el modelo qwen-coder para escribir una función Python que calcule fibonacci
```

Expected: response includes the generated text from the Pollinations qwen-coder sub-agent.

- [ ] **Step 5: Verify parallel execution (two tools at once)**

```
genera una imagen de un robot y también crea un audio diciendo "Hello, I am your robot assistant"
```

Expected: both `generate_image` and `generate_audio` detected, executed in parallel (both appear in `[Media artifacts generated]`), total time ~10s (not ~20s).

- [ ] **Step 6: Commit if any cleanup required**

```powershell
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "fix: Pollinations tools cleanup after e2e verification"
```

Only needed if verification revealed issues requiring changes.
