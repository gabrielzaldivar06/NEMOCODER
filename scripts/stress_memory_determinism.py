"""
Stress test: verifica determinismo de la recuperación de memoria NEMO.

Envía N rondas de consultas al chat de Space Code y mide:
- Tasa de éxito (respuesta correcta)
- Estabilidad de latencia (min/max/p50/p95)
- Consistencia entre rondas (misma respuesta siempre)
- Fallos intermitentes (flakiness)

Uso:
    python scripts/stress_memory_determinism.py [--rounds N] [--parallel K]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any


BACKEND = "http://127.0.0.1:8787"

PROBES = [
    {
        "label": "identidad_nombre",
        "message": "Como me llamo?",
        "expect_contains": ["gabriel"],
        "expect_not_contains": ["no encontre", "no se ha proporcionado", "no tengo"],
    },
    {
        "label": "identidad_saludo",
        "message": "Hola! Sabes como me llamo?",
        "expect_contains": ["gabriel"],
        "expect_not_contains": ["no se ha proporcionado"],
    },
    {
        "label": "proyecto",
        "message": "Que proyecto estamos desarrollando?",
        "expect_contains": ["mission control", "space code"],
        "expect_not_contains": [],
    },
    {
        "label": "saludo_simple",
        "message": "hola",
        "expect_contains": [],
        "expect_not_contains": ["error", "traceback"],
    },
]


@dataclass
class RunResult:
    label: str
    message: str
    round_n: int
    total_ms: float
    response: str
    ok: bool
    failures: list[str] = field(default_factory=list)
    error: str = ""


def post_chat(message: str, backend: str, history: list[dict] | None = None, timeout: float = 120.0) -> tuple[str, float]:
    payload = json.dumps({
        "message": message,
        "history": history or [],
        "chat_mode": "chat",
    }).encode()
    req = urllib.request.Request(
        f"{backend}/api/agent/message",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    ms = (time.perf_counter() - t0) * 1000
    content = data.get("message", {}).get("content", "")
    return content, ms


def run_probe(probe: dict, round_n: int, backend: str = BACKEND) -> RunResult:
    label = probe["label"]
    message = probe["message"]
    try:
        response, ms = post_chat(message, backend)
    except Exception as exc:
        return RunResult(
            label=label, message=message, round_n=round_n,
            total_ms=0.0, response="", ok=False, error=str(exc)
        )

    response_lower = response.lower()
    failures: list[str] = []

    for term in probe.get("expect_contains", []):
        if term.lower() not in response_lower:
            failures.append(f"missing '{term}'")

    for term in probe.get("expect_not_contains", []):
        if term.lower() in response_lower:
            failures.append(f"found banned '{term}'")

    return RunResult(
        label=label, message=message, round_n=round_n,
        total_ms=ms, response=response[:200],
        ok=len(failures) == 0,
        failures=failures,
    )


def print_bar(label: str, value: float, max_val: float, width: int = 30) -> str:
    filled = int(width * value / max_val) if max_val > 0 else 0
    bar = "#" * filled + "-" * (width - filled)
    return f"  [{bar}] {value:.0f}ms  {label}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5, help="Rondas por probe")
    parser.add_argument("--parallel", type=int, default=1, help="Llamadas paralelas (1=secuencial)")
    parser.add_argument("--backend", default=BACKEND)
    args = parser.parse_args()

    backend = args.backend.rstrip("/")

    # Health check
    try:
        with urllib.request.urlopen(f"{backend}/api/health", timeout=5) as resp:
            health = json.loads(resp.read())
        print(f"Backend: {health.get('status')}  NEMO: {health.get('nemo', {}).get('available')}")
    except Exception as exc:
        print(f"Backend no disponible: {exc}")
        return

    total_probes = len(PROBES)
    total_runs = total_probes * args.rounds

    print(f"\nStress test: {total_probes} probes x {args.rounds} rondas = {total_runs} runs")
    print(f"Paralelismo: {args.parallel}  Backend: {BACKEND}")
    print("=" * 70)

    all_results: list[RunResult] = []
    work_items = [
        (probe, r)
        for r in range(1, args.rounds + 1)
        for probe in PROBES
    ]

    if args.parallel > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as ex:
            futures = {ex.submit(run_probe, probe, r, backend): (probe, r) for probe, r in work_items}
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                result = fut.result()
                all_results.append(result)
                status = "OK" if result.ok else "FAIL"
                print(f"  [{i:2}/{total_runs}] {result.label:<25} {result.total_ms:6.0f}ms  {status}"
                      + (f"  {result.failures}" if result.failures else "")
                      + (f"  ERR: {result.error[:60]}" if result.error else ""))
    else:
        for i, (probe, r) in enumerate(work_items, 1):
            result = run_probe(probe, r, backend)
            all_results.append(result)
            status = "OK" if result.ok else "FAIL"
            print(f"  [{i:2}/{total_runs}] r{r} {result.label:<25} {result.total_ms:6.0f}ms  {status}"
                  + (f"  {result.failures}" if result.failures else "")
                  + (f"  ERR: {result.error[:60]}" if result.error else ""))

    # --- Summary ---
    print("\n" + "=" * 70)
    print("RESUMEN POR PROBE")
    print("=" * 70)

    by_label: dict[str, list[RunResult]] = {}
    for r in all_results:
        by_label.setdefault(r.label, []).append(r)

    all_ms: list[float] = []
    total_ok = 0
    total_fail = 0

    max_ms = max((r.total_ms for r in all_results if not r.error), default=1.0)

    for label, results in by_label.items():
        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        total_ok += ok_count
        total_fail += fail_count

        valid_ms = [r.total_ms for r in results if not r.error]
        all_ms.extend(valid_ms)

        if valid_ms:
            avg = statistics.mean(valid_ms)
            p50 = statistics.median(valid_ms)
            p95 = sorted(valid_ms)[int(len(valid_ms) * 0.95)] if len(valid_ms) >= 2 else valid_ms[-1]
            mn, mx = min(valid_ms), max(valid_ms)
        else:
            avg = p50 = p95 = mn = mx = 0.0

        success_pct = ok_count / len(results) * 100
        print(f"\n  {label}")
        print(f"    Exito: {ok_count}/{len(results)} ({success_pct:.0f}%)")
        print(f"    Latencia: avg={avg:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms  min={mn:.0f}ms  max={mx:.0f}ms")
        print(print_bar("avg", avg, max_ms))

        # Collect unique failure messages
        failure_msgs: dict[str, int] = {}
        for r in results:
            for f in r.failures:
                failure_msgs[f] = failure_msgs.get(f, 0) + 1
        if failure_msgs:
            print(f"    Fallos: {dict(failure_msgs)}")

        # Check response consistency
        responses = [r.response[:80] for r in results if not r.error]
        unique_resp = set(responses)
        if len(unique_resp) == 1:
            print(f"    Consistencia: DETERMINISTA (respuesta identica en todas las rondas)")
        elif len(unique_resp) <= 2:
            print(f"    Consistencia: CASI-DETERMINISTA ({len(unique_resp)} variantes)")
        else:
            print(f"    Consistencia: VARIABLE ({len(unique_resp)} variantes distintas en {len(results)} runs)")

    print("\n" + "=" * 70)
    print("GLOBAL")
    print("=" * 70)
    total = total_ok + total_fail
    print(f"  Exito total : {total_ok}/{total} ({total_ok/total*100:.1f}%)")
    if all_ms:
        print(f"  Latencia    : avg={statistics.mean(all_ms):.0f}ms  "
              f"p50={statistics.median(all_ms):.0f}ms  "
              f"p95={sorted(all_ms)[int(len(all_ms)*0.95)]:.0f}ms  "
              f"min={min(all_ms):.0f}ms  max={max(all_ms):.0f}ms")
    flaky = [label for label, results in by_label.items()
             if 0 < sum(1 for r in results if r.ok) < len(results)]
    if flaky:
        print(f"  Flaky probes: {flaky}")
    else:
        print(f"  Flakiness   : ninguna (todos los probes son 100% o 0%)")

    verdict = "PASS" if total_fail == 0 else "FAIL"
    print(f"\n  VEREDICTO: {verdict}")


if __name__ == "__main__":
    main()
