"""
Test: multi-turn handoff thread continuity.

Simulates a realistic handoff conversation where a specific task/run is introduced
at turn 1 and the agent must recall it in later turns. Measures at which turn
context degrades or breaks.

Uso:
    python scripts/test_handoff_thread_continuity.py [--backend URL] [--max-turns N] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from typing import Any


BACKEND = "http://127.0.0.1:8787"

# ---------------------------------------------------------------------------
# Thread definition
# Each turn has:
#   message       - what the user sends
#   anchors       - keywords the response MUST mention to be coherent
#   forbidden     - keywords that indicate context loss
#   coherence_fn  - optional lambda(response_lower) -> bool override
# ---------------------------------------------------------------------------
THREAD = [
    {
        "turn": 1,
        "label": "inicio_tarea",
        "message": (
            "Necesito que analices el modulo de autenticacion del proyecto "
            "Mission Control. El identificador de esta tarea es TASK-AUTH-42. "
            "Empieza por describir que archivos revisarias."
        ),
        # accent-folded: "autenticacion" matches "autenticación"
        "anchors": ["autenticacion", "mission control"],
        "forbidden": ["no tengo", "no se ha proporcionado", "traceback"],
        "note": "Introduce la tarea y el ID TASK-AUTH-42",
    },
    {
        "turn": 2,
        "label": "seguimiento_archivos",
        "message": "Bien. Que posibles vulnerabilidades encontrarias en esos archivos?",
        "anchors": [],
        "forbidden": ["no entiendo", "cual tarea", "traceback"],
        "note": "El agente debe recordar el contexto de autenticacion",
    },
    {
        "turn": 3,
        "label": "recall_id_tarea",
        "message": "Recuerdas el identificador de la tarea que estamos trabajando?",
        "anchors": ["task-auth-42", "auth-42", "42"],
        "forbidden": ["no recuerdo", "no tengo acceso", "no se ha proporcionado"],
        "note": "Recall directo del ID TASK-AUTH-42",
    },
    {
        "turn": 4,
        "label": "profundizacion",
        "message": (
            "Perfecto. Ahora describe como repararias la vulnerabilidad mas critica "
            "que mencionaste. Sigue en el contexto de la tarea TASK-AUTH-42."
        ),
        "anchors": [],
        "forbidden": ["error", "traceback"],
        "note": "Profundizacion sobre la tarea, reinyecta el ID",
    },
    {
        "turn": 5,
        "label": "recall_proyecto",
        "message": "En que proyecto estamos trabajando y cual es su proposito principal?",
        "anchors": ["mission control"],
        "forbidden": ["no se", "no tengo informacion"],
        "note": "Recall de contexto de proyecto via NEMO",
    },
    {
        "turn": 6,
        "label": "recall_usuario",
        "message": "Y como me llamo yo, el usuario?",
        "anchors": ["gabriel"],
        "forbidden": ["no se ha proporcionado", "no tengo", "no conozco"],
        "note": "Recall de identidad del usuario via NEMO",
    },
    {
        "turn": 7,
        "label": "recall_id_sin_hint",
        "message": (
            "Sin que te lo recuerde, dime: cual era el identificador de la tarea "
            "de autenticacion que empezamos al inicio de esta conversacion?"
        ),
        "anchors": ["task-auth-42", "auth-42", "42"],
        "forbidden": ["no recuerdo", "no tengo acceso", "no me has dicho"],
        "note": "Recall duro del ID sin reinyectar el contexto",
    },
    {
        "turn": 8,
        "label": "resumen_completo",
        "message": (
            "Haz un resumen de todo lo que hemos analizado en esta sesion: "
            "la tarea, los archivos, las vulnerabilidades y el plan de reparacion."
        ),
        "anchors": ["auth", "task"],
        "forbidden": ["no tenemos", "no hemos", "error"],
        "note": "Capacidad de sintesis del hilo completo",
    },
]


@dataclass
class TurnResult:
    turn: int
    label: str
    note: str
    message: str
    response: str
    total_ms: float
    anchors_found: list[str]
    anchors_missing: list[str]
    forbidden_found: list[str]
    coherent: bool
    error: str = ""


def post_chat(message: str, history: list[dict], backend: str, timeout: float = 180.0) -> tuple[str, float]:
    payload = json.dumps({
        "message": message,
        "history": history,
        "chat_mode": "chat",
    }).encode("utf-8")
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


def _fold(text: str) -> str:
    """Lowercase + strip diacritics so accent differences don't cause mismatches."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


def evaluate_turn(turn_def: dict, response: str, ms: float) -> TurnResult:
    resp_folded = _fold(response)
    anchors_found = [a for a in turn_def["anchors"] if _fold(a) in resp_folded]
    anchors_missing = [a for a in turn_def["anchors"] if _fold(a) not in resp_folded]
    forbidden_found = [f for f in turn_def["forbidden"] if _fold(f) in resp_folded]

    # Coherent = all anchors found AND no forbidden terms
    coherent = len(anchors_missing) == 0 and len(forbidden_found) == 0

    # If no anchors defined, coherent = no forbidden terms AND response is non-empty
    if not turn_def["anchors"]:
        coherent = len(forbidden_found) == 0 and len(response.strip()) > 20

    return TurnResult(
        turn=turn_def["turn"],
        label=turn_def["label"],
        note=turn_def["note"],
        message=turn_def["message"],
        response=response,
        total_ms=ms,
        anchors_found=anchors_found,
        anchors_missing=anchors_missing,
        forbidden_found=forbidden_found,
        coherent=coherent,
    )


def truncate(text: str, n: int = 160) -> str:
    text = text.replace("\n", " ")
    return text[:n] + "..." if len(text) > n else text


def bar(coherent: bool) -> str:
    return "[OK]" if coherent else "[FAIL]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=BACKEND)
    parser.add_argument("--max-turns", type=int, default=len(THREAD))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")

    try:
        with urllib.request.urlopen(f"{backend}/api/health", timeout=5) as resp:
            health = json.loads(resp.read())
        print(f"Backend: {health.get('status')}  NEMO: {health.get('nemo', {}).get('available')}")
    except Exception as exc:
        print(f"Backend no disponible: {exc}")
        return

    turns_to_run = THREAD[:args.max_turns]
    print(f"\nTest hilo multi-turno: {len(turns_to_run)} turnos")
    print("=" * 75)

    history: list[dict] = []
    results: list[TurnResult] = []
    first_fail_turn: int | None = None

    for turn_def in turns_to_run:
        t = turn_def["turn"]
        label = turn_def["label"]
        print(f"\n  Turno {t}/{len(turns_to_run)} [{label}]")
        print(f"  Pregunta: {truncate(turn_def['message'], 100)}")
        print(f"  Nota   : {turn_def['note']}")
        print(f"  Enviando...", end=" ", flush=True)

        try:
            response, ms = post_chat(turn_def["message"], list(history), backend)
            result = evaluate_turn(turn_def, response, ms)
        except Exception as exc:
            result = TurnResult(
                turn=t, label=label, note=turn_def["note"],
                message=turn_def["message"], response="", total_ms=0.0,
                anchors_found=[], anchors_missing=turn_def["anchors"],
                forbidden_found=[], coherent=False, error=str(exc),
            )

        results.append(result)
        status = bar(result.coherent)
        print(f"{status}  ({result.total_ms:.0f}ms)")

        if result.error:
            print(f"  ERROR: {result.error}")
        else:
            print(f"  Respuesta: {truncate(result.response)}")
            if result.anchors_found:
                print(f"  Anchors OK : {result.anchors_found}")
            if result.anchors_missing:
                print(f"  Falta      : {result.anchors_missing}")
            if result.forbidden_found:
                print(f"  Prohibido  : {result.forbidden_found}")
            if args.verbose:
                print(f"\n  --- Respuesta completa ---\n{result.response}\n  ---")

        if not result.coherent and first_fail_turn is None:
            first_fail_turn = t

        # Update history for next turn
        if not result.error:
            history.append({"role": "user", "content": turn_def["message"]})
            history.append({"role": "assistant", "content": result.response[:800]})

    # --- Summary ---
    print("\n" + "=" * 75)
    print("RESUMEN DE CONTINUIDAD")
    print("=" * 75)

    coherent_count = sum(1 for r in results if r.coherent)
    total = len(results)
    all_ms = [r.total_ms for r in results if not r.error and r.total_ms > 0]

    print(f"\n  Turnos coherentes : {coherent_count}/{total}")
    if all_ms:
        import statistics
        print(f"  Latencia          : avg={statistics.mean(all_ms):.0f}ms  "
              f"min={min(all_ms):.0f}ms  max={max(all_ms):.0f}ms")

    print()
    print(f"  {'Turno':<6} {'Label':<28} {'Coherente':<10} {'ms':<8} Problema")
    print(f"  {'-'*6} {'-'*28} {'-'*10} {'-'*8} {'-'*30}")
    for r in results:
        problem = ""
        if r.error:
            problem = f"ERR: {r.error[:40]}"
        elif r.anchors_missing:
            problem = f"falta: {r.anchors_missing}"
        elif r.forbidden_found:
            problem = f"prohibido: {r.forbidden_found}"
        flag = "SI" if r.coherent else "NO"
        print(f"  {r.turn:<6} {r.label:<28} {flag:<10} {r.total_ms:<8.0f} {problem}")

    print()
    if first_fail_turn is None:
        print(f"  RESULTADO: Hilo COMPLETO mantenido en los {total} turnos  (PASS)")
    else:
        coherent_before = first_fail_turn - 1
        print(f"  RESULTADO: Coherencia mantenida hasta turno {coherent_before}")
        print(f"             Primera degradacion en turno {first_fail_turn} [{results[first_fail_turn-1].label}]")
        if coherent_before >= 5:
            verdict = "ACEPTABLE"
        elif coherent_before >= 3:
            verdict = "PARCIAL"
        else:
            verdict = "CRITICO"
        print(f"             Veredicto de continuidad: {verdict}")

    print()


if __name__ == "__main__":
    main()
