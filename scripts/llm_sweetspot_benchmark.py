from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CHAT_PROMPTS = {
    "tiny": "Responde exactamente: pong",
    "short_es": "En una frase, dime si estas operativo.",
    "code_tiny": "Devuelve solo una funcion Python llamada add que sume dos numeros.",
}

MISSION_CONTROL_SCENARIOS = {
    "ordinary_chat": "ping de diagnostico rapido",
    "memory_lookup": "RECUERDAS DVE",
    "technical_short": "Explica en dos frases como diagnosticar un endpoint lento.",
}

NON_CHAT_MARKERS = ("embed", "embedding", "rerank", "reranker", "mmproj", "bge-")


@dataclass(frozen=True, slots=True)
class HttpResult:
    ok: bool
    elapsed_ms: float
    payload: dict[str, Any]
    error: str = ""


def _post_json(url: str, payload: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None) -> HttpResult:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - started) * 1000
        return HttpResult(True, elapsed_ms, json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        body = error.read().decode("utf-8", errors="replace")[:1200]
        return HttpResult(False, elapsed_ms, {}, f"HTTP {error.code}: {body}")
    except Exception as error:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        return HttpResult(False, elapsed_ms, {}, str(error))


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> HttpResult:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - started) * 1000
        return HttpResult(True, elapsed_ms, json.loads(body) if body else {})
    except Exception as error:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        return HttpResult(False, elapsed_ms, {}, str(error))


def _looks_non_chat_model(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in NON_CHAT_MARKERS)


def _model_file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "parent": path.parent.name,
        "size_bytes": path.stat().st_size,
        "size_gb": round(path.stat().st_size / (1024**3), 2),
    }


def _quality_check(prompt: str, content: str) -> tuple[bool, list[str]]:
    text = content.strip()
    lowered = text.lower().strip(" .`\n\t")
    issues: list[str] = []
    if prompt == CHAT_PROMPTS["tiny"] and lowered != "pong":
        issues.append("tiny prompt did not return exactly pong")
    if prompt == CHAT_PROMPTS["code_tiny"]:
        if "def add" not in lowered:
            issues.append("code prompt missing def add")
        if "return" not in lowered:
            issues.append("code prompt missing return")
    if prompt == CHAT_PROMPTS["short_es"] and not text:
        issues.append("short Spanish prompt returned empty text")
    return not issues, issues


def discover_lmstudio_model_files(root: str, *, limit: int) -> list[dict[str, Any]]:
    root_path = Path(root).expanduser()
    if not root_path.exists():
        return []
    records = []
    for path in root_path.rglob("*.gguf"):
        candidate_name = " ".join(path.parts[-4:])
        if _looks_non_chat_model(candidate_name):
            continue
        records.append(_model_file_record(path))
    records.sort(key=lambda item: int(item["size_bytes"]), reverse=True)
    return records[: max(1, limit)]


def _ollama_tag_for_path(path: str, prefix: str) -> str:
    model_path = Path(path)
    slug_source = f"{model_path.parent.name}-{model_path.stem}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")[:52]
    digest = hashlib.sha1(str(model_path).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{slug}-{digest}:latest"


def import_gguf_to_ollama(path: str, *, tag_prefix: str, modelfile_dir: Path, timeout: float) -> dict[str, Any]:
    model_path = Path(path)
    tag = _ollama_tag_for_path(str(model_path), tag_prefix)
    modelfile_dir.mkdir(parents=True, exist_ok=True)
    modelfile_path = modelfile_dir / f"{tag.replace(':', '-').replace('/', '-')}.Modelfile"
    from_path = model_path.resolve().as_posix()
    modelfile_path.write_text(f'FROM "{from_path}"\n', encoding="utf-8")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            ["ollama", "create", tag, "-f", str(modelfile_path)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": completed.returncode == 0,
            "tag": tag,
            "source_path": str(model_path),
            "modelfile": str(modelfile_path),
            "elapsed_ms": round(elapsed_ms, 1),
            "stdout": completed.stdout[-1200:],
            "stderr": completed.stderr[-1200:],
            "returncode": completed.returncode,
        }
    except Exception as error:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "tag": tag,
            "source_path": str(model_path),
            "modelfile": str(modelfile_path),
            "elapsed_ms": round(elapsed_ms, 1),
            "stdout": "",
            "stderr": str(error),
            "returncode": None,
        }


def discover_lmstudio_models(base_url: str, *, timeout: float) -> list[str]:
    result = _get_json(f"{base_url.rstrip('/')}/models", timeout=timeout, headers={"Authorization": "Bearer lm-studio"})
    if not result.ok:
        return []
    models = []
    for item in result.payload.get("data", []):
        model_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(model_id, str):
            continue
        lowered = model_id.lower()
        if _looks_non_chat_model(lowered):
            continue
        models.append(model_id)
    return models


def discover_ollama_models(base_url: str, *, timeout: float) -> list[str]:
    result = _get_json(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    if not result.ok:
        return []
    models = []
    for item in result.payload.get("models", []):
        name = item.get("name") if isinstance(item, dict) else None
        lowered = name.lower() if isinstance(name, str) else ""
        if _looks_non_chat_model(lowered):
            continue
        if isinstance(name, str) and name.strip():
            models.append(name)
    return models


def lmstudio_chat(base_url: str, model: str, prompt: str, *, max_tokens: int, temperature: float, top_p: float, timeout: float) -> dict[str, Any]:
    result = _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=timeout,
        headers={"Authorization": "Bearer lm-studio"},
    )
    content = ""
    usage = None
    if result.ok:
        choices = result.payload.get("choices") if isinstance(result.payload, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        usage = result.payload.get("usage") if isinstance(result.payload, dict) else None
    quality_pass, quality_issues = _quality_check(prompt, content)
    return {
        "provider": "lmstudio",
        "model": model,
        "prompt": prompt,
        "params": {"max_tokens": max_tokens, "temperature": temperature, "top_p": top_p},
        "ok": result.ok,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "empty": not bool(content.strip()),
        "quality_pass": quality_pass,
        "quality_issues": quality_issues,
        "chars": len(content),
        "content_preview": content[:240],
        "usage": usage,
        "error": result.error,
    }


def ollama_chat(base_url: str, model: str, prompt: str, *, num_predict: int, temperature: float, num_ctx: int, timeout: float) -> dict[str, Any]:
    result = _post_json(
        f"{base_url.rstrip('/')}/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
        },
        timeout=timeout,
    )
    content = ""
    if result.ok:
        message = result.payload.get("message") if isinstance(result.payload, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "")
    quality_pass, quality_issues = _quality_check(prompt, content)
    return {
        "provider": "ollama",
        "model": model,
        "prompt": prompt,
        "params": {"num_predict": num_predict, "temperature": temperature, "num_ctx": num_ctx},
        "ok": result.ok,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "empty": not bool(content.strip()),
        "quality_pass": quality_pass,
        "quality_issues": quality_issues,
        "chars": len(content),
        "content_preview": content[:240],
        "error": result.error,
    }


def _trace_segments(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    running: dict[str, tuple[datetime, str]] = {}
    rows: list[dict[str, Any]] = []
    for event in trace:
        label = str(event.get("label") or "")
        status = event.get("status")
        timestamp = event.get("ts")
        if not isinstance(timestamp, str):
            continue
        current = datetime.fromisoformat(timestamp)
        if status == "running":
            key = str(event.get("tool_name") or label)
            running[key] = (current, label)
            continue
        keys = [str(event.get("tool_name") or ""), label]
        if label.startswith("tool: "):
            keys.append(label[len("tool: "):].split(" (alias ")[0])
        if "lmstudio.chat_completions" in label:
            keys.append("model_generation")
        for key in keys:
            if key in running:
                started, start_label = running.pop(key)
                rows.append(
                    {
                        "label": label,
                        "start_label": start_label,
                        "status": status,
                        "ms": round((current - started).total_seconds() * 1000, 1),
                    }
                )
                break
    return rows


def _component_costs(segments: list[dict[str, Any]]) -> dict[str, float]:
    costs = {"nemo_mcp_ms": 0.0, "model_ms": 0.0, "server_other_ms": 0.0}
    for segment in segments:
        label = str(segment.get("label") or "")
        start_label = str(segment.get("start_label") or "")
        ms = float(segment.get("ms") or 0.0)
        if "nemo_memory." in label:
            costs["nemo_mcp_ms"] += ms
        elif "lmstudio" in label or start_label == "model_generation":
            costs["model_ms"] += ms
        elif label != "status":
            costs["server_other_ms"] += ms
    return {key: round(value, 1) for key, value in costs.items()}


def mission_control_chat(base_url: str, model: str, message: str, *, max_tokens: int, temperature: float, timeout: float) -> dict[str, Any]:
    result = _post_json(
        f"{base_url.rstrip('/')}/api/agent/message",
        {
            "message": message,
            "provider": "subprocess",
            "require_nemo_mcp_capabilities": True,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    content = ""
    segments: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    if result.ok:
        message_payload = result.payload.get("message") if isinstance(result.payload, dict) else None
        if isinstance(message_payload, dict):
            content = str(message_payload.get("content") or "")
            raw_trace = message_payload.get("agent_trace")
            segments = _trace_segments(raw_trace if isinstance(raw_trace, list) else [])
            raw_tools = message_payload.get("tool_calls")
            if isinstance(raw_tools, list):
                tools = [item for item in raw_tools if isinstance(item, dict)]
    tool_names = [str(tool.get("name") or "") for tool in tools]
    costs = _component_costs(segments)
    content_is_empty = not bool(content.strip()) or content.strip() == "The model returned an empty response."
    return {
        "provider": "mission_control",
        "model": model,
        "scenario": message,
        "params": {"max_tokens": max_tokens, "temperature": temperature, "require_nemo_mcp_capabilities": True},
        "ok": result.ok,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "empty": content_is_empty,
        "chars": len(content),
        "content_preview": content[:240],
        "component_costs": costs,
        "segments": segments,
        "nemo_tool_count": sum(1 for name in tool_names if name.startswith("nemo_memory.")),
        "has_nemo_context_bootstrap": "nemo_memory.context_bootstrap" in tool_names,
        "has_nemo_store_conversation": "nemo_memory.store_conversation" in tool_names,
        "has_verified_nemo_answer": "mission_control.verified_nemo_answer" in tool_names,
        "tool_summaries": [
            {"name": tool.get("name"), "status": tool.get("status"), "summary": tool.get("summary")}
            for tool in tools
        ],
        "error": result.error,
    }


def score_result(result: dict[str, Any]) -> float:
    if not result.get("ok") or result.get("empty"):
        return -1_000_000.0
    if result.get("quality_pass") is False:
        return -500_000.0
    elapsed = float(result.get("elapsed_ms") or 0.0)
    score = 100_000.0 - elapsed
    if result.get("provider") == "mission_control":
        if not result.get("has_nemo_context_bootstrap"):
            score -= 50_000.0
        if not result.get("has_nemo_store_conversation"):
            score -= 10_000.0
    return round(score, 2)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        key = f"{result.get('provider')}::{result.get('model')}::{json.dumps(result.get('params'), sort_keys=True)}"
        grouped.setdefault(key, []).append(result)
    summaries = []
    for key, items in grouped.items():
        elapsed_values = [float(item.get("elapsed_ms") or 0.0) for item in items if item.get("ok")]
        scores = [score_result(item) for item in items]
        summaries.append(
            {
                "key": key,
                "provider": items[0].get("provider"),
                "model": items[0].get("model"),
                "params": items[0].get("params"),
                "runs": len(items),
                "ok_runs": sum(1 for item in items if item.get("ok")),
                "empty_runs": sum(1 for item in items if item.get("empty")),
                "quality_fail_runs": sum(1 for item in items if item.get("quality_pass") is False),
                "avg_elapsed_ms": round(statistics.mean(elapsed_values), 1) if elapsed_values else None,
                "median_elapsed_ms": round(statistics.median(elapsed_values), 1) if elapsed_values else None,
                "avg_score": round(statistics.mean(scores), 2) if scores else None,
            }
        )
    summaries.sort(key=lambda item: (item.get("avg_score") is None, -(item.get("avg_score") or -1_000_000)))
    viable = [
        item
        for item in summaries
        if (item.get("avg_score") or -1_000_000) > 0
        and item.get("empty_runs") == 0
        and item.get("quality_fail_runs") == 0
    ]
    return {"groups": summaries, "winner": viable[0] if viable else None}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = ["# LLM Sweet Spot Benchmark", ""]
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")
    winner = report.get("summary", {}).get("winner")
    if isinstance(winner, dict):
        lines.append("## Current Winner")
        lines.append("")
        lines.append(f"- Provider/model: `{winner.get('provider')}` / `{winner.get('model')}`")
        lines.append(f"- Params: `{json.dumps(winner.get('params'), ensure_ascii=False)}`")
        lines.append(f"- Avg elapsed ms: `{winner.get('avg_elapsed_ms')}`")
        lines.append(f"- Empty runs: `{winner.get('empty_runs')}`")
        lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Provider | Model | Params | Runs | Empty / Quality Fail | Avg ms | Score |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for item in report.get("summary", {}).get("groups", [])[:20]:
        lines.append(
            "| {provider} | {model} | `{params}` | {runs} | {empty} | {avg} | {score} |".format(
                provider=item.get("provider"),
                model=item.get("model"),
                params=json.dumps(item.get("params"), ensure_ascii=False),
                runs=item.get("runs"),
                empty=f"{item.get('empty_runs')} / qfail {item.get('quality_fail_runs', 0)}",
                avg=item.get("avg_elapsed_ms"),
                score=item.get("avg_score"),
            )
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Mission Control runs require real NEMO MCP capability checks and record NEMO tool presence.")
    lines.append("- Runtime quantization is not changed by this script; compare loaded quantized model variants instead.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark LM Studio/Ollama/Mission Control for an LLM sweet spot.")
    parser.add_argument("--lmstudio-base-url", default="http://localhost:1234/v1")
    parser.add_argument("--mission-control-url", default="http://127.0.0.1:8787")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--lmstudio-models-root", default=str(Path.home() / ".lmstudio" / "models"))
    parser.add_argument("--models", default="", help="Comma-separated LM Studio model ids. Defaults to discovered chat models.")
    parser.add_argument("--ollama-models", default="", help="Comma-separated Ollama model names. Defaults to discovered models.")
    parser.add_argument("--gguf-model-paths", default="", help="Comma-separated absolute GGUF paths to import/test in Ollama.")
    parser.add_argument("--discover-gguf", action="store_true", help="Discover local LM Studio GGUF model files.")
    parser.add_argument("--import-gguf-to-ollama", action="store_true", help="Create Ollama model tags from discovered or explicit GGUF paths.")
    parser.add_argument("--ollama-tag-prefix", default="lmstudio")
    parser.add_argument("--import-timeout", type=float, default=900.0)
    parser.add_argument("--max-models", type=int, default=4)
    parser.add_argument("--max-tokens", default="32,96")
    parser.add_argument("--temperatures", default="0.0,0.2")
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--ollama-num-ctx", default="2048,4096")
    parser.add_argument("--prompt-names", default="tiny,short_es,code_tiny", help="Comma-separated prompt names to run.")
    parser.add_argument("--mission-scenarios", default="ordinary_chat,memory_lookup,technical_short", help="Comma-separated Mission Control scenario names to run.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--include-mission-control", action="store_true")
    parser.add_argument("--include-ollama", action="store_true")
    parser.add_argument("--skip-lmstudio", action="store_true")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_json = Path(args.output_json) if args.output_json else artifacts / f"llm-sweetspot-{stamp}.json"
    output_md = Path(args.output_md) if args.output_md else artifacts / f"llm-sweetspot-{stamp}.md"

    lm_models = [] if args.skip_lmstudio else (_parse_csv(args.models) or discover_lmstudio_models(args.lmstudio_base_url, timeout=min(args.timeout, 20.0)))
    lm_models = lm_models[: max(1, args.max_models)]
    ollama_models = _parse_csv(args.ollama_models)
    gguf_records = discover_lmstudio_model_files(args.lmstudio_models_root, limit=args.max_models) if args.discover_gguf else []
    gguf_paths = _parse_csv(args.gguf_model_paths) or [str(item["path"]) for item in gguf_records]
    import_results: list[dict[str, Any]] = []
    if args.import_gguf_to_ollama:
        for gguf_path in gguf_paths[: max(1, args.max_models)]:
            import_row = import_gguf_to_ollama(
                gguf_path,
                tag_prefix=args.ollama_tag_prefix,
                modelfile_dir=artifacts / "ollama-modelfiles",
                timeout=args.import_timeout,
            )
            import_results.append(import_row)
            if import_row["ok"] and import_row["tag"] not in ollama_models:
                ollama_models.append(str(import_row["tag"]))
    if args.include_ollama and not ollama_models:
        ollama_models = discover_ollama_models(args.ollama_base_url, timeout=min(args.timeout, 20.0))[: max(1, args.max_models)]
    max_tokens_values = [int(item) for item in _parse_csv(args.max_tokens)]
    temperature_values = [float(item) for item in _parse_csv(args.temperatures)]
    ollama_ctx_values = [int(item) for item in _parse_csv(args.ollama_num_ctx)]
    prompt_names = _parse_csv(args.prompt_names)
    mission_scenario_names = _parse_csv(args.mission_scenarios)
    prompts = {name: CHAT_PROMPTS[name] for name in prompt_names if name in CHAT_PROMPTS}
    mission_scenarios = {name: MISSION_CONTROL_SCENARIOS[name] for name in mission_scenario_names if name in MISSION_CONTROL_SCENARIOS}

    results: list[dict[str, Any]] = []
    for _ in range(max(1, args.repeats)):
        for model in lm_models:
            for max_tokens in max_tokens_values:
                for temperature in temperature_values:
                    for prompt_name, prompt in prompts.items():
                        row = lmstudio_chat(
                            args.lmstudio_base_url,
                            model,
                            prompt,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=args.top_p,
                            timeout=args.timeout,
                        )
                        row["prompt_name"] = prompt_name
                        results.append(row)
                    if args.include_mission_control:
                        for scenario_name, message in mission_scenarios.items():
                            row = mission_control_chat(
                                args.mission_control_url,
                                model,
                                message,
                                max_tokens=max_tokens,
                                temperature=temperature,
                                timeout=args.timeout + 60,
                            )
                            row["scenario_name"] = scenario_name
                            results.append(row)
        if args.include_ollama:
            for model in ollama_models:
                for num_predict in max_tokens_values:
                    for temperature in temperature_values:
                        for num_ctx in ollama_ctx_values:
                            for prompt_name, prompt in prompts.items():
                                row = ollama_chat(
                                    args.ollama_base_url,
                                    model,
                                    prompt,
                                    num_predict=num_predict,
                                    temperature=temperature,
                                    num_ctx=num_ctx,
                                    timeout=args.timeout,
                                )
                                row["prompt_name"] = prompt_name
                                results.append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "lmstudio_base_url": args.lmstudio_base_url,
            "mission_control_url": args.mission_control_url,
            "ollama_base_url": args.ollama_base_url,
            "lm_models": lm_models,
            "ollama_models": ollama_models,
            "lmstudio_models_root": args.lmstudio_models_root,
            "discovered_gguf_models": gguf_records,
            "gguf_model_paths": gguf_paths[: max(1, args.max_models)],
            "max_tokens": max_tokens_values,
            "temperatures": temperature_values,
            "top_p": args.top_p,
            "ollama_num_ctx": ollama_ctx_values,
            "prompt_names": list(prompts),
            "mission_scenarios": list(mission_scenarios),
            "repeats": args.repeats,
            "include_mission_control": args.include_mission_control,
            "include_ollama": args.include_ollama,
            "skip_lmstudio": args.skip_lmstudio,
            "import_gguf_to_ollama": args.import_gguf_to_ollama,
            "ollama_tag_prefix": args.ollama_tag_prefix,
        },
        "ollama_imports": import_results,
        "results": results,
        "summary": summarize(results),
    }
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(output_md, report)
    print(json.dumps({"ok": True, "json": str(output_json), "markdown": str(output_md), "winner": report["summary"].get("winner")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())