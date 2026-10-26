"""Read-only model performance projections from durable Agent runs."""
from __future__ import annotations

from datetime import datetime, timezone


WINDOW_SIZE = 50


def _iso(value: str | None) -> str:
    return value or ""


def _is_timeout(value: dict) -> bool:
    return str((value.get("error") or {}).get("code") or "").endswith("TIMEOUT")


def _model_attempts(store, model_id: str) -> list[dict]:
    """Return the newest distinct brain-level calls using one deployment."""
    rows: list[dict] = []
    for run in store.list("agent_run"):
        if run.get("brain") == "unified":
            for brain_run in (run.get("brain_runs") or {}).values():
                if brain_run.get("model_deployment_id") == model_id:
                    rows.append({
                        "status": brain_run.get("status"),
                        # provider_roundtrip_ms excludes queueing/retrieval;
                        # retain the legacy brain duration as a fallback for
                        # historical runs created before this telemetry.
                        "duration_ms": brain_run.get("provider_roundtrip_ms")
                        if isinstance(brain_run.get("provider_roundtrip_ms"), (int, float))
                        else brain_run.get("model_duration_ms")
                        if isinstance(brain_run.get("model_duration_ms"), (int, float))
                        else brain_run.get("duration_ms"),
                        "retrieval_duration_ms": brain_run.get("retrieval_duration_ms"),
                        "ttft_ms": brain_run.get("ttft_ms"),
                        "generation_duration_ms": brain_run.get("generation_duration_ms"),
                        "error": brain_run.get("error"),
                        "at": brain_run.get("ended_at") or run.get("ended_at") or run.get("updated_at"),
                    })
            merge_run = run.get("merge_run") or {}
            if merge_run.get("model_deployment_id") == model_id:
                rows.append({
                    "status": merge_run.get("status"),
                    "duration_ms": merge_run.get("provider_roundtrip_ms")
                    if isinstance(merge_run.get("provider_roundtrip_ms"), (int, float))
                    else merge_run.get("duration_ms"),
                    "retrieval_duration_ms": merge_run.get("retrieval_duration_ms"),
                    "ttft_ms": merge_run.get("ttft_ms"),
                    "generation_duration_ms": merge_run.get("generation_duration_ms"),
                    "error": merge_run.get("error"),
                    "at": merge_run.get("ended_at") or run.get("ended_at") or run.get("updated_at"),
                })
        elif run.get("model_deployment_id") == model_id:
            rows.append({
                "status": run.get("status"),
                "duration_ms": run.get("provider_roundtrip_ms") or run.get("elapsed_ms"),
                "retrieval_duration_ms": run.get("retrieval_duration_ms"),
                "ttft_ms": run.get("ttft_ms"),
                "generation_duration_ms": run.get("generation_duration_ms"),
                "error": run.get("error"),
                "at": run.get("ended_at") or run.get("updated_at"),
            })
    return sorted(rows, key=lambda item: _iso(item.get("at")), reverse=True)[:WINDOW_SIZE]


def model_inference_stats(store, model_id: str) -> dict:
    """Summarise a bounded recent window without exposing prompts or credentials."""
    attempts = _model_attempts(store, model_id)
    durations = sorted(int(item["duration_ms"]) for item in attempts
                       if isinstance(item.get("duration_ms"), (int, float)) and item["duration_ms"] >= 0)
    generation = sorted(int(item["generation_duration_ms"]) for item in attempts
                        if isinstance(item.get("generation_duration_ms"), (int, float)) and item["generation_duration_ms"] >= 0)
    ttft = sorted(int(item["ttft_ms"]) for item in attempts
                  if isinstance(item.get("ttft_ms"), (int, float)) and item["ttft_ms"] >= 0)
    retrieval = sorted(int(item["retrieval_duration_ms"]) for item in attempts
                       if isinstance(item.get("retrieval_duration_ms"), (int, float)) and item["retrieval_duration_ms"] >= 0)
    timeouts = [item for item in attempts if _is_timeout(item)]
    failures = [item for item in attempts if item.get("status") in {"failed", "unavailable"}]
    p95_index = max(0, (len(durations) * 95 + 99) // 100 - 1) if durations else None
    generation_p95_index = max(0, (len(generation) * 95 + 99) // 100 - 1) if generation else None
    ttft_p95_index = max(0, (len(ttft) * 95 + 99) // 100 - 1) if ttft else None
    return {
        "window_size": WINDOW_SIZE,
        "attempt_count": len(attempts),
        "success_count": sum(item.get("status") == "succeeded" for item in attempts),
        "failure_count": len(failures),
        "timeout_count": len(timeouts),
        "timeout_rate": round(len(timeouts) / len(attempts) * 100, 1) if attempts else 0,
        "average_duration_ms": round(sum(durations) / len(durations)) if durations else None,
        "p95_duration_ms": durations[p95_index] if p95_index is not None else None,
        "average_retrieval_duration_ms": round(sum(retrieval) / len(retrieval)) if retrieval else None,
        "average_ttft_ms": round(sum(ttft) / len(ttft)) if ttft else None,
        "p95_ttft_ms": ttft[ttft_p95_index] if ttft_p95_index is not None else None,
        "generation_count": len(generation),
        "average_generation_duration_ms": round(sum(generation) / len(generation)) if generation else None,
        "p95_generation_duration_ms": generation[generation_p95_index] if generation_p95_index is not None else None,
        "latest_at": attempts[0].get("at") if attempts else None,
        "last_timeout_at": timeouts[0].get("at") if timeouts else None,
        "last_failure_code": (failures[0].get("error") or {}).get("code") if failures else None,
    }
