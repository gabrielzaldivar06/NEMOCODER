"""
Test: autonomous plan mode loop with NEMO checkpoints and self-critique.

Sends a creative coding task to /api/agent/plan and watches the agent iterate
from a rough first attempt toward acceptable quality, saving checkpoints to NEMO
after each iteration.

Uso:
    python scripts/test_plan_mode_loop.py [--backend URL] [--iterations N] [--threshold SCORE] [--save-code PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


BACKEND = "http://127.0.0.1:8787"

OBJECTIVE = (
    "Dibuja una mano humana usando Python y matplotlib. "
    "La mano debe incluir: palma, cinco dedos con proporciones realistas, "
    "nudillos visibles, una muneca, y al menos 3 colores distintos (piel, lineas, sombra). "
    "El resultado debe guardarse como 'hand.png' y mostrarse en pantalla si es posible."
)


def post(url: str, payload: dict, timeout: float = 600.0) -> tuple[dict, float]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data, time.perf_counter() - t0


def bar(score: float, width: int = 20) -> str:
    filled = int(width * score / 10)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {score:.1f}/10"


def print_iteration(it: dict) -> None:
    n = it["iteration"]
    score = it["score"]
    exec_ok = it["exec_ok"]
    code_chars = it["code_chars"]
    critique = it["critique_summary"]
    exec_out = it.get("exec_output", "")

    print(f"\n  --- Iteracion {n} ---")
    print(f"  Score   : {bar(score)}")
    print(f"  Codigo  : {code_chars} chars")
    print(f"  Ejecuta : {'SI' if exec_ok else 'NO'}  {exec_out[:80] if exec_out else ''}")
    # Extract present/missing from critique JSON if possible
    try:
        start = critique.index("{")
        end = critique.rindex("}") + 1
        parsed = json.loads(critique[start:end])
        present = parsed.get("present", [])
        missing = parsed.get("missing", [])
        improvements = parsed.get("improvements", [])
        summary = parsed.get("summary", "")
        if present:
            print(f"  Presente: {', '.join(str(p) for p in present[:4])}")
        if missing:
            print(f"  Falta   : {', '.join(str(m) for m in missing[:4])}")
        if summary:
            print(f"  Resumen : {summary[:120]}")
        if improvements:
            print(f"  Mejoras : {improvements[0][:100]}" + (" ..." if len(improvements) > 1 else ""))
    except (ValueError, json.JSONDecodeError, KeyError):
        # Fallback: just print raw critique
        print(f"  Critica : {critique[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=BACKEND)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=7.0, help="Quality score 1-10 to stop iterating")
    parser.add_argument("--save-code", default="hand.py", help="Where to save the final generated code")
    parser.add_argument("--objective", default=None, help="Override the default objective")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")
    objective = args.objective or OBJECTIVE

    # Health check
    try:
        with urllib.request.urlopen(f"{backend}/api/health", timeout=5) as resp:
            health = json.loads(resp.read())
        print(f"Backend: {health.get('status')}  NEMO: {health.get('nemo', {}).get('available')}")
    except Exception as exc:
        print(f"Backend no disponible: {exc}")
        sys.exit(1)

    print(f"\nPlan Mode Loop — Objetivo:")
    print(f"  {objective[:120]}...")
    print(f"\nParametros: max_iterations={args.iterations}  threshold={args.threshold}/10")
    print("=" * 70)
    print("\nEnviando a /api/agent/plan ...\n")

    payload = {
        "objective": objective,
        "max_iterations": args.iterations,
        "quality_threshold": args.threshold,
        "topic": "human_hand_drawing",
    }

    t0 = time.perf_counter()
    try:
        data, elapsed = post(f"{backend}/api/agent/plan", payload, timeout=1800.0)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    total_s = time.perf_counter() - t0

    if not data.get("ok"):
        print(f"FALLO: {data}")
        sys.exit(1)

    # Print each iteration
    iterations = data.get("iterations", [])
    for it in iterations:
        print_iteration(it)

    # Summary
    print("\n" + "=" * 70)
    print("RESUMEN FINAL")
    print("=" * 70)
    print(f"  Iteraciones ejecutadas : {data['iterations_run']}")
    print(f"  Puntuacion final       : {bar(data['final_score'])}")
    print(f"  Umbral de calidad      : {data['quality_threshold']}/10")
    print(f"  Completado             : {'SI' if data['completed'] else 'NO (limite de iteraciones alcanzado)'}")
    print(f"  Tiempo total           : {total_s:.0f}s")

    # Score progression
    if iterations:
        scores = [it["score"] for it in iterations]
        print(f"\n  Progresion de score: {' -> '.join(f'{s:.1f}' for s in scores)}")
        delta = scores[-1] - scores[0]
        print(f"  Mejora total       : {delta:+.1f} puntos en {len(scores)} iteraciones")

    # Save final code
    final_code = data.get("final_code", "")
    if final_code:
        save_path = Path(args.save_code)
        save_path.write_text(final_code, encoding="utf-8")
        print(f"\n  Codigo final guardado en: {save_path} ({len(final_code)} chars)")

        # Try to run the saved file
        print(f"\n  Intentando ejecutar {save_path} ...")
        import subprocess
        try:
            result = subprocess.run(
                [sys.executable, str(save_path)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                print(f"  Ejecucion: OK")
                if result.stdout:
                    print(f"  Output: {result.stdout[:200]}")
            else:
                print(f"  Ejecucion: FALLO (returncode={result.returncode})")
                if result.stderr:
                    print(f"  Error: {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            print(f"  Ejecucion: timeout (>15s) — probablemente abriendo ventana grafica")
        except Exception as exc:
            print(f"  Ejecucion: error — {exc}")

    print()


if __name__ == "__main__":
    main()
