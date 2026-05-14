"""Quick direct test of api_agent_plan_gen without the HTTP server."""
import sys, time
sys.path.insert(0, "src")

from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    api_agent_plan_gen,
    _resolve_lmstudio_model,
)

BASE_URL = "http://localhost:1234/v1"
model = _resolve_lmstudio_model(BASE_URL)
print(f"Model auto-detected: {model!r}")

config = MissionControlServerConfig.from_paths(
    repo=".",
    runtimes=".spacecode-runtimes",
    apply_results=".spacecode-runtimes/apply",
    memory_db=None,
)

payload = {
    "objective": "Dibuja una mano humana simple con matplotlib: palma y 5 dedos. Guarda como hand.png.",
    "max_iterations": 2,
    "quality_threshold": 7.0,
    "topic": "human_hand_drawing",
    "parallel_candidates": False,
    "visual_critique": False,
    "model_base_url": BASE_URL,
    "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
}

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
        print(f"[{t}s] ITER {i}  score={s}/10  exec_ok={ok}  chars={chars}  {out}")
    elif etype == "done":
        n = event["iterations_run"]
        fs = event["final_score"]
        done = event["completed"]
        print(f"[{t}s] DONE   {n} iterations  final={fs}/10  completed={done}")
    elif etype == "error":
        print(f"[{t}s] ERROR  {event.get('error')}")

print("Done.")
