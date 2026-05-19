"""Quick direct test of api_agent_plan_gen without the HTTP server.

Usage:
  python scripts/_quick_plan_test.py                        # LM Studio (auto-detect)
  python scripts/_quick_plan_test.py --nim                  # NIM, model from settings
  python scripts/_quick_plan_test.py --nim --model meta/llama-3.2-11b-vision-instruct
  python scripts/_quick_plan_test.py --nim --model microsoft/phi-4-multimodal-instruct
"""
import sys, time
sys.path.insert(0, "src")

from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    _load_settings,
    api_agent_plan_gen,
    _resolve_lmstudio_model,
)

USE_NIM = "--nim" in sys.argv
MODEL_OVERRIDE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--model=")), None)
# Support --model <value> (space-separated) as well
if not MODEL_OVERRIDE:
    try:
        idx = sys.argv.index("--model")
        MODEL_OVERRIDE = sys.argv[idx + 1]
    except (ValueError, IndexError):
        pass

config = MissionControlServerConfig.from_paths(
    repo=".",
    runtimes=".spacecode-runtimes",
    apply_results=".spacecode-runtimes/apply",
    memory_db=None,
)

if USE_NIM:
    settings = _load_settings(config)
    BASE_URL = str(settings.get("model_base_url") or "https://integrate.api.nvidia.com/v1")
    API_KEY = str(settings.get("api_key") or "lm-studio")
    # Explicit override > settings default_model > auto-resolve
    DEFAULT_MODEL = MODEL_OVERRIDE or str(settings.get("default_model") or "meta/llama-3.2-11b-vision-instruct")
    model = _resolve_lmstudio_model(BASE_URL, api_key=API_KEY, default_model=DEFAULT_MODEL) or DEFAULT_MODEL
    print(f"NIM endpoint: {BASE_URL}")
    print(f"Model: {model!r}")
else:
    BASE_URL = "http://localhost:1234/v1"
    API_KEY = "lm-studio"
    model = MODEL_OVERRIDE or _resolve_lmstudio_model(BASE_URL)
    print(f"LM Studio endpoint: {BASE_URL}")
    print(f"Model: {model!r}")

payload = {
    "objective": "Dibuja una mano humana simple con matplotlib: palma y 5 dedos. Guarda como hand.png.",
    "max_iterations": 2,
    "quality_threshold": 7.0,
    "topic": "human_hand_drawing",
    "parallel_candidates": False,
    "visual_critique": False,
    "model_base_url": BASE_URL,
    "api_key": API_KEY,
    "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
}
if model:
    payload["default_model"] = model

t0 = time.perf_counter()
for event in api_agent_plan_gen(config, payload):
    t = round(time.perf_counter() - t0, 1)
    etype = event.get("type")
    if etype == "start":
        print(f"[{t}s] START  max_iter={event['max_iterations']}")
    elif etype == "iteration":
        i = event["iteration"]
        s = event["score"]
        ok = event["exec_ok"]
        chars = event["code_chars"]
        out = (event.get("exec_output") or "")[:80]
        print(f"[{t}s] ITER {i}  score={s}/10  exec_ok={ok}  chars={chars}  {out!r}")
    elif etype == "done":
        n = event["iterations_run"]
        fs = event["final_score"]
        bs = event.get("best_score", fs)
        sr = event.get("stop_reason", "max_iterations")
        done = event["completed"]
        print(f"[{t}s] DONE   {n} iterations  final={fs}/10  best={bs}/10  stop={sr}  completed={done}")
    elif etype == "error":
        print(f"[{t}s] ERROR  {event.get('error')}")

print("Done.")
