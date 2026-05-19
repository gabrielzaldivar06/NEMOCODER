# Pollinations Media Tools — Design Spec

**Date:** 2026-05-19

## Goal

Expose four media generation capabilities (image, audio, video, text) as tools the active model can invoke directly from chat or from within the plan loop. Execution is backend-only, parallel, and model-agnostic — works with LM Studio local models and remote API-key endpoints (NVIDIA NIM, etc.) equally.

## Architecture

Change is confined to `src/nemo_coding_platform/mission_control_server.py`. No new endpoints. No frontend changes. No new processes.

**Flow:**
```
active model (any: LM Studio local OR remote API-key endpoint)
    │
    │  emits tool calls (native OpenAI function calling OR JSON in text)
    ▼
api_agent_message
    │
    │  _parse_llm_tool_calls() detects N Pollinations invocations
    │
    ├─ generate_image  ─┐
    ├─ generate_audio  ─┤  ThreadPoolExecutor → N simultaneous HTTP calls
    ├─ generate_video  ─┤  to api.pollinations.ai (no _LLM_SEM needed)
    └─ generate_text   ─┘
                        │
                        ▼
              artifacts saved to:
              .spacecode-runtimes/mission-control/artifacts/
                  images/, audio/, video/, text/
                        │
                        ▼
              role="system" messages injected into context
                        │
                        ▼
              re-query active model → final response to user
```

The same mechanism operates inside `api_agent_plan_gen`: the generator detects Pollinations tool calls in the generation response, executes them in parallel, and passes artifact paths into the next critique prompt.

## The Four Tools

### `generate_image`
```
params:
  prompt        (required) — image description
  model         (default "flux") — flux | flux-realism | flux-anime | gpt-image-1 | seedream-3 | kontext
  width         (default 1024)
  height        (default 1024)
  seed          (optional integer)
  enhance       (default true) — Pollinations prompt enhancement

HTTP: GET https://image.pollinations.ai/prompt/{prompt}?model=&width=&height=&seed=&enhance=
saves: artifacts/images/{uuid}.jpg
returns: {"artifact_path": "...", "url": "...", "model_used": "..."}
```

### `generate_audio`
```
params:
  text          (required) — text to synthesize
  voice         (default "nova") — alloy | echo | fable | onyx | nova | shimmer
                                   + ElevenLabs: heart | aria | adam | bill | brian | callum | charlie
                                                 charlotte | chris | daniel | emily | eric | freya |
                                                 george | grace | harry | james | jessica | laura |
                                                 liam | lily | matilda | river | roger | sarah | will
  model         (default "openai-audio")

HTTP: GET https://audio.pollinations.ai/{text}?voice=&model=
saves: artifacts/audio/{uuid}.mp3
returns: {"artifact_path": "...", "url": "...", "voice_used": "..."}
```

### `generate_video`
```
params:
  prompt            (required) — video description
  model             (default "seedance-1-lite") — seedance-1-lite | wan-fast | veo-2
  duration          (default 5, seconds)
  keyframe_image    (optional) — absolute path to an existing artifact image to use as first frame

HTTP: POST https://video.pollinations.ai/
      polls until status=done (timeout: 120s, poll interval: 5s)
saves: artifacts/video/{uuid}.mp4
returns: {"artifact_path": "...", "url": "...", "model_used": "..."}
```

### `generate_text`
```
params:
  prompt    (required) — task for the text sub-agent
  model     (default "openai") — openai | qwen-coder | deepseek | mistral | claude-hybridspace | gemini-2.0
  system    (optional) — system instruction for the sub-agent
  seed      (optional integer)

HTTP: GET https://text.pollinations.ai/{prompt}?model=&system=&seed=
does NOT save to disk — result is returned as plain string in the tool message
returns: {"text": "...", "model_used": "..."}
```

`generate_text` is the "text sub-agent" pattern: the active model delegates a subtask to a different Pollinations text model and receives the result in its context to continue reasoning.

## Execution in `api_agent_message`

After obtaining the model response:

```python
POLLINATIONS_TOOLS = {"generate_image", "generate_audio", "generate_video", "generate_text"}

# 1. Detect Pollinations invocations
media_calls = [inv for inv in _parse_llm_tool_calls(response)
               if inv["tool"] in POLLINATIONS_TOOLS]

# 2. Execute in parallel (no _LLM_SEM — these are external HTTP, not LM calls)
results = {}
if media_calls:
    with ThreadPoolExecutor(max_workers=len(media_calls)) as ex:
        futures = {ex.submit(_execute_pollinations_tool, inv): inv for inv in media_calls}
        for fut in as_completed(futures):
            inv = futures[fut]
            results[inv["id"]] = fut.result()  # dict or {"error": "..."}

# 3. Inject results as system message (no tool_call_id needed — native IDs are lost
#    during serialization in _lmstudio_chat_completion; system messages match existing pattern)
results_text = "\n".join(
    f"[{inv['tool']} result]: {json.dumps(results[inv['tool']])}"
    for inv in media_calls
)
tool_result_msg = {"role": "system", "content": f"[Pollinations tool results]\n{results_text}"}

# 4. Re-query model with artifacts available
if media_calls:
    final_response = _lmstudio_chat_completion({..., "messages": [*messages, tool_result_msg]})
```

## Execution in `api_agent_plan_gen`

After each generation step, before the critique call:

1. Parse Pollinations tool calls from the generated code/response.
2. Execute in parallel (same `ThreadPoolExecutor` pattern).
3. Append artifact paths to the critique prompt:
   ```
   [Media artifacts generated this iteration]
   - artifacts/images/abc123.jpg (generate_image: "spiral chart")
   ```
4. Critique model can reference artifacts in its score and improvements.

## Tool Catalog Integration

Add to both `_AGENT_TOOL_CATALOG` (string, text fallback) and `_AGENT_TOOL_SCHEMAS` (OpenAI function calling, used when model supports native tool calls).

String catalog entry format follows existing pattern (numbered, params listed, constraints noted).

Schema entries follow existing `{"type": "function", "function": {...}}` structure.

## Backend Helper: `_execute_pollinations_tool`

Single dispatcher function:

```python
def _execute_pollinations_tool(inv: dict) -> dict:
    tool = inv["tool"]
    params = inv.get("params", {})
    if tool == "generate_image":   return _pollinations_image(params)
    if tool == "generate_audio":   return _pollinations_audio(params)
    if tool == "generate_video":   return _pollinations_video(params)
    if tool == "generate_text":    return _pollinations_text(params)
    return {"error": f"unknown tool: {tool}"}
```

Each `_pollinations_*` function:
- Builds the URL / POST body
- Makes the HTTP request (urllib, no new dependencies)
- Saves artifact to the appropriate subdirectory under `artifacts/`
- Returns a result dict or `{"error": "..."}` on failure

Artifact directories are created on first use (same pattern as existing `images/` dir).

## Error Handling

- HTTP timeout → `{"error": "timeout after Xs"}` in tool message
- HTTP non-200 → `{"error": "HTTP {status}: {body[:200]}"}` in tool message  
- Video polling timeout (>120s) → `{"error": "video generation timed out"}` in tool message
- The model receives the error and can continue, retry with different params, or report to user

`_LLM_SEM` is not acquired for any Pollinations call — these are external HTTP, not Arc iGPU inference.

## Artifact Directory Structure

```
.spacecode-runtimes/mission-control/artifacts/
    images/   ← existing (plan loop PNGs) + new generate_image JPEGs
    audio/    ← new MP3s from generate_audio
    video/    ← new MP4s from generate_video
    (text results are not persisted — returned inline as tool message content)
```

## Files Changed

- `src/nemo_coding_platform/mission_control_server.py`
  - Add `POLLINATIONS_TOOLS` constant (module-level Set)
  - Add `_execute_pollinations_tool(inv)` dispatcher
  - Add `_pollinations_image(params)`, `_pollinations_audio(params)`, `_pollinations_video(params)`, `_pollinations_text(params)`
  - Extend `_AGENT_TOOL_CATALOG` string with 4 new tools
  - Extend `_AGENT_TOOL_SCHEMAS` list with 4 new function schemas
  - Extend `api_agent_message`: detect + parallel-execute + inject tool messages + re-query
  - Extend `api_agent_plan_gen`: detect + parallel-execute + inject artifact paths into critique prompt

## Out of Scope

- Frontend UI changes (artifact display already works via existing artifact studio)
- New HTTP endpoints (everything is backend-internal)
- Audio STT / transcription (different use case, separate feature)
- Rate limiting / Pollinations API auth (API is free/public, no auth needed)
- Caching of generated artifacts (future optimization)
