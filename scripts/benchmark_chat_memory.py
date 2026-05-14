"""
Benchmark Space Code chat endpoint — mide tiempos de recuperación de memoria NEMO
y latencia de inferencia LM Studio por fase del agente.

Uso:
    python scripts/benchmark_chat_memory.py [--runs N] [--backend http://127.0.0.1:8787]

Métricas capturadas:
- Tiempo total del endpoint /api/agent/message
- Tiempo acumulado de cada tool NEMO (context_bootstrap, search_memories, etc.)
- Tiempo de inferencia LM Studio (fase "lm_studio" del trace)
- Overhead del harness Python (total - NEMO - LM Studio)
- Desglose por paso del agent_trace
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from typing import Any


BACKEND = "http://127.0.0.1:8787"

MESSAGES = [
    ("identidad", "¿Cómo me llamo?"),
    ("saludo_simple", "hola"),
    ("memoria_contexto", "¿Qué proyecto estamos desarrollando?"),
    ("codigo_simple", "Dame un ejemplo Python de una función que sume dos números"),
]


def post(url: str, payload: dict[str, Any], timeout: float = 120.0) -> tuple[dict, float]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    elapsed = time.perf_counter() - t0
    return json.loads(raw), elapsed


def extract_trace_timing(agent_trace: list[dict]) -> dict[str, float]:
    """Suma duración de pasos por categoría (kind/label) del agent_trace."""
    buckets: dict[str, float] = {}
    for event in agent_trace:
        kind = str(event.get("kind") or event.get("label") or "unknown")
        dur = float(event.get("duration_ms") or 0)
        buckets[kind] = buckets.get(kind, 0.0) + dur
    return buckets


def nemo_tool_ms(tool_calls: list[dict]) -> dict[str, float]:
    """Suma duración de cada herramienta NEMO del array tool_calls."""
    totals: dict[str, float] = {}
    for tc in tool_calls:
        name = str(tc.get("tool") or tc.get("name") or "?")
        dur = float(tc.get("duration_ms") or tc.get("elapsed_ms") or 0)
        totals[name] = totals.get(name, 0.0) + dur
    return totals


def lm_studio_ms_from_trace(agent_trace: list[dict]) -> float:
    """Extrae tiempo de inferencia LM Studio de eventos con kind=lm_studio o label que lo indique."""
    total = 0.0
    for event in agent_trace:
        kind = str(event.get("kind") or "")
        label = str(event.get("label") or "")
        if kind in ("lm_studio", "inference", "llm") or "lm_studio" in label or "inference" in label:
            total += float(event.get("duration_ms") or 0)
    return total


def run_benchmark(backend: str, label: str, message: str, history: list[dict]) -> dict:
    payload = {
        "message": message,
        "history": history,
        "chat_mode": "chat",
        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
    }

    try:
        data, total_s = post(f"{backend}/api/agent/message", payload)
    except Exception as exc:
        return {"label": label, "error": str(exc)}

    msg = data.get("message") or {}
    tool_calls: list[dict] = msg.get("tool_calls") or []
    agent_trace: list[dict] = msg.get("agent_trace") or []
    response_text = str(msg.get("content") or "")

    trace_buckets = extract_trace_timing(agent_trace)
    nemo_tools = nemo_tool_ms(tool_calls)
    lm_ms = lm_studio_ms_from_trace(agent_trace)

    total_ms = total_s * 1000
    nemo_total_ms = sum(nemo_tools.values())
    overhead_ms = max(0.0, total_ms - nemo_total_ms - lm_ms)

    return {
        "label": label,
        "message": message,
        "response_preview": response_text[:120].replace("\n", " "),
        "total_ms": round(total_ms, 1),
        "lm_studio_ms": round(lm_ms, 1),
        "nemo_total_ms": round(nemo_total_ms, 1),
        "overhead_ms": round(overhead_ms, 1),
        "nemo_tools": {k: round(v, 1) for k, v in sorted(nemo_tools.items(), key=lambda x: -x[1])},
        "trace_buckets": {k: round(v, 1) for k, v in sorted(trace_buckets.items(), key=lambda x: -x[1])},
        "tool_calls_count": len(tool_calls),
        "trace_events_count": len(agent_trace),
    }


def print_result(r: dict, run_num: int = 1) -> None:
    if "error" in r:
        print(f"  ERROR [{r['label']}]: {r['error']}")
        return

    total = r["total_ms"]
    lm = r["lm_studio_ms"]
    nemo = r["nemo_total_ms"]
    overhead = r["overhead_ms"]

    lm_pct = (lm / total * 100) if total else 0
    nemo_pct = (nemo / total * 100) if total else 0
    overhead_pct = (overhead / total * 100) if total else 0

    print(f"\n  Run #{run_num} - [{r['label']}] \"{r['message']}\"")
    print(f"  -> Total: {total:.0f} ms")
    print(f"    |-- LM Studio inferencia : {lm:7.0f} ms  ({lm_pct:.0f}%)")
    print(f"    |-- NEMO MCP tools       : {nemo:7.0f} ms  ({nemo_pct:.0f}%)")
    print(f"    +-- Overhead harness     : {overhead:7.0f} ms  ({overhead_pct:.0f}%)")

    if r["nemo_tools"]:
        print(f"    NEMO por tool:")
        for tool, ms in r["nemo_tools"].items():
            print(f"      * {tool:<35} {ms:7.0f} ms")

    print(f"    Respuesta: {r['response_preview'][:100]}...")
    print(f"    Tool calls: {r['tool_calls_count']}  |  Trace events: {r['trace_events_count']}")


def print_summary(all_results: list[dict]) -> None:
    valid = [r for r in all_results if "error" not in r]
    if not valid:
        print("\nNo valid results for summary.")
        return

    print("\n" + "=" * 65)
    print("RESUMEN ESTADÍSTICO")
    print("=" * 65)

    totals = [r["total_ms"] for r in valid]
    lms = [r["lm_studio_ms"] for r in valid]
    nemos = [r["nemo_total_ms"] for r in valid]
    overheads = [r["overhead_ms"] for r in valid]

    def stats(vals: list[float]) -> str:
        if not vals:
            return "n/a"
        return f"avg={statistics.mean(vals):.0f}ms  min={min(vals):.0f}  max={max(vals):.0f}  p50={statistics.median(vals):.0f}"

    print(f"  Total endpoint  : {stats(totals)}")
    print(f"  LM Studio infer : {stats(lms)}")
    print(f"  NEMO MCP tools  : {stats(nemos)}")
    print(f"  Overhead Python : {stats(overheads)}")

    # NEMO tool breakdown across all runs
    all_tool_ms: dict[str, list[float]] = {}
    for r in valid:
        for tool, ms in r["nemo_tools"].items():
            all_tool_ms.setdefault(tool, []).append(ms)

    if all_tool_ms:
        print("\n  NEMO avg por tool (sobre todos los runs):")
        for tool, vals in sorted(all_tool_ms.items(), key=lambda x: -statistics.mean(x[1])):
            print(f"    * {tool:<35} avg={statistics.mean(vals):.0f}ms  calls={len(vals)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="Repeticiones por mensaje")
    parser.add_argument("--backend", default=BACKEND)
    parser.add_argument("--message", default=None, help="Mensaje único personalizado")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")

    # Verificar que el backend responde
    try:
        with urllib.request.urlopen(f"{backend}/api/health", timeout=5) as resp:
            health = json.loads(resp.read())
        print(f"Backend: {health.get('status', '?')} | NEMO: {health.get('nemo', {}).get('available', '?')}")
        print(f"NEMO transport: SSE={health.get('nemo', {}).get('available')} @ 8765")
    except Exception as exc:
        print(f"Backend no disponible: {exc}")
        return

    if args.message:
        msgs_to_test = [("custom", args.message)]
    else:
        msgs_to_test = MESSAGES

    all_results: list[dict] = []
    history: list[dict] = []

    print(f"\n{'='*65}")
    print(f"BENCHMARK CHAT + MEMORIA — {args.runs} run(s) por mensaje")
    print(f"Backend: {backend}  |  Mensajes: {len(msgs_to_test)}")
    print(f"{'='*65}")

    for label, message in msgs_to_test:
        for run_n in range(1, args.runs + 1):
            print(f"\n[{label}] Enviando run {run_n}/{args.runs}...", end=" ", flush=True)
            result = run_benchmark(backend, label, message, list(history))
            all_results.append(result)
            print(f"{'OK' if 'error' not in result else 'ERROR'} ({result.get('total_ms', '?'):.0f}ms)")
            print_result(result, run_num=run_n)

            # Acumular historial para simular conversación real
            if "error" not in result:
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": result["response_preview"]})
                history = history[-12:]  # Máximo 12 mensajes

    if len(all_results) > 1:
        print_summary(all_results)


if __name__ == "__main__":
    main()
