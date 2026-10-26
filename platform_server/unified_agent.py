"""Durable 3+1 orchestration for the single Agent entry point."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter

import httpx
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from platform_server.agent_runs import (
    SYSTEM_PROMPT, evidence_context, fast_evidence_prompt,
    fast_reference_note, fast_system_prompt, medical_reference_note,
)
from platform_server.ingestion import KnowledgeWorkspaceWarmingError
from platform_server.model_provider import inference_error
from platform_server.store import now, uid
from brains.knowledge.src.temporal import infer_query_plan


BRAINS = ("memory", "knowledge", "structured")
BRAIN_LABELS = {
    "memory": "时空记忆推理脑",
    "knowledge": "知识数据推理脑",
    "structured": "结构化数据推理脑",
}
ROUTE_KEYWORDS = {
    "structured": (
        "销量", "销售", "金额", "数量", "指标", "同比", "环比", "汇总", "合计", "统计", "多少",
        "增长率", "占比", "均值", "总量", "排行", "排名", "数据集", "各月", "各单位", "明细",
    ),
    "knowledge": (
        "规则", "定义", "依据", "条款", "标准", "为什么", "知识", "文档", "规范", "政策", "法律",
        "解释", "案例", "条件", "例外", "原理", "办法", "应该", "怎么办", "如何", "注意事项",
        "去年", "前年", "今年", "内容时间", "资料时间", "导入资料",
    ),
    "memory": (
        "记得", "照片", "视频", "语音", "录音", "上次", "曾经", "我拍", "回忆", "影像", "拍摄",
        "那天", "当面", "当时", "见到", "听到", "在哪里", "发生过", "场景", "动作", "摄像头",
        "昨天", "前天", "最近看", "听别人说", "我最近",
    ),
}


def route_query(query: str, mode: str = "auto", requested: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
    if mode == "scoped":
        selected = [brain for brain in BRAINS if brain in (requested or [])]
        if not selected:
            raise ValueError("限定路由至少选择一个认知脑")
        return selected, {brain: "用户明确限定本次核验范围" for brain in selected}
    hits = {
        brain: [keyword for keyword in keywords if keyword in (query or "")]
        for brain, keywords in ROUTE_KEYWORDS.items()
    }
    selected = [brain for brain in BRAINS if hits[brain]]
    if not selected:
        selected = list(BRAINS)
        return selected, {brain: "问题意图不唯一，按 3+1 策略并行核验" for brain in selected}
    return selected, {brain: "命中能力词：" + "、".join(hits[brain][:5]) for brain in selected}


def _claim_count(result: dict) -> int:
    return len((result.get("result") or {}).get("claims") or [])


def _safe_error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


class UnifiedAgentRuns:
    def __init__(self, store, ingest, client, *, agent_url: str):
        self.store = store
        self.ingest = ingest
        self.client = client
        self.agent_url = agent_url.rstrip("/")
        self._cluster_projection_cache: dict[str, tuple[object, list[dict], dict[str, list[dict]]]] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def pause(self, owner: str, session_id: str, run_id: str) -> dict:
        run = self.store.get("agent_run", owner, run_id)
        if not run or run.get("session_id") != session_id or run.get("brain") != "unified":
            raise HTTPException(404, "统一 Agent 运行不存在或无访问权限")
        if run.get("status") != "running":
            raise HTTPException(409, "只有运行中的统一 Agent 可以暂停")
        for step in reversed(run.get("steps", [])):
            if step.get("status") == "running":
                step.update(status="paused", ended_at=now(), duration_ms=round((time.monotonic() - step.get("started_epoch", time.monotonic())) * 1000),
                            detail="用户暂停；已完成的星团和脑核验结果已保留")
                break
        run.update(status="paused", overall_status="paused", paused_at=now(), ended_at=now(),
                   error=_safe_error("PAUSED_BY_USER", "已暂停本次统一执行；未生成新的合并回答。"))
        self._save(run)
        task = self._tasks.get((owner, run_id))
        if task and not task.done():
            task.cancel()
        self.store.audit(owner, "unified_agent.paused", session_id, {"run_id": run_id})
        return run

    def retract(self, owner: str, session_id: str, run_id: str) -> dict:
        run = self.store.get("agent_run", owner, run_id)
        if not run or run.get("session_id") != session_id or run.get("brain") != "unified":
            raise HTTPException(404, "统一 Agent 运行不存在或无访问权限")
        if run.get("status") not in {"paused", "failed"}:
            raise HTTPException(409, "请先暂停运行，再撤回问题")
        conversation = self.store.get("conversation", owner, session_id)
        if not conversation:
            raise HTTPException(404, "会话不存在或无访问权限")
        conversation["messages"] = [item for item in conversation.get("messages", []) if item.get("run_id") != run_id]
        conversation["last_run_id"] = next((item.get("run_id") for item in reversed(conversation["messages"]) if item.get("run_id")), None)
        self.store.put("conversation", owner, conversation)
        self.store.delete("agent_run", owner, run_id)
        self._tasks.pop((owner, run_id), None)
        self.store.audit(owner, "unified_agent.retracted", session_id, {"run_id": run_id})
        return conversation

    async def submit(self, owner: str, session_id: str, body) -> dict:
        task = asyncio.create_task(self.execute(owner, session_id, body))
        for _ in range(8):
            await asyncio.sleep(0)
            conversation = self.store.get("conversation", owner, session_id)
            run = self.store.get("agent_run", owner, conversation.get("last_run_id", "")) if conversation else None
            if run and run.get("status") == "running":
                self._tasks[(owner, run["id"])] = task
                task.add_done_callback(lambda _done, key=(owner, run["id"]): self._tasks.pop(key, None))
                conversation["run"] = run
                return conversation
            if task.done():
                return await task
        task.cancel()
        raise HTTPException(500, "无法创建可暂停的统一运行记录")

    def _chat_models(self) -> list[dict]:
        return [item for item in self.store.list("model", "system")
                if item.get("purpose") == "chat" and item.get("enabled")]

    def _default_deployment(self) -> str:
        rows = self._chat_models()
        default = next((row for row in rows if row.get("is_default") and row.get("deployment")), None)
        if default:
            return default["deployment"]
        # Codex is the installation default when rows predate the explicit flag.
        codex = next((row for row in rows if row.get("deployment") == "codex"), None)
        return ((codex or (rows[0] if rows else {})).get("deployment") or "")

    def _active_model(self, brain: str, purpose: str = "chat", deployment: str = "") -> dict | None:
        rows = [item for item in self.store.list("model", "system")
                if item.get("brain") == brain and item.get("purpose") == purpose and item.get("enabled")]
        if purpose == "chat":
            if deployment:
                selected = next((row for row in rows if (row.get("deployment") or "") == deployment), None)
                if selected:
                    return selected
            default = next((row for row in rows if row.get("is_default")), None)
            if default:
                return default
        return rows[0] if rows else None

    async def enrich_visualizations(self, owner: str, conversation: dict) -> dict:
        """Backfill chart artifacts for unified answers saved before chart propagation."""
        if conversation.get("brain") != "unified":
            return conversation
        token = os.environ.get("ENGINE_SERVICE_TOKEN", "")
        if not token:
            return conversation
        pending = []
        for message in conversation.get("messages", []):
            if message.get("role") != "assistant" or message.get("visualizations"):
                continue
            run = self.store.get("agent_run", owner, message.get("run_id", ""))
            structured = ((run or {}).get("brain_runs", {}).get("structured", {}) or {})
            child_id = structured.get("child_run_id")
            child_session_id = structured.get("child_session_id")
            if child_id:
                pending.append((message, run, child_id, child_session_id))
        if not pending:
            return conversation
        changed = False
        headers = {"X-Service-Token": token}
        async with self.client(15) as connection:
            for message, run, child_id, child_session_id in pending[:8]:
                try:
                    response = await connection.get(self.agent_url + f"/api/v1/agent/runs/{child_id}", headers=headers)
                    child = response.json() if response.status_code == 200 else {}
                    if not child.get("visualizations") and child_session_id:
                        session_response = await connection.get(
                            self.agent_url + f"/api/v1/agent/sessions/{child_session_id}", headers=headers,
                        )
                        if session_response.status_code == 200:
                            session = session_response.json()
                            child = next((item for item in reversed(session.get("messages", []))
                                          if item.get("role") == "assistant" and item.get("run_id") == child_id), child)
                    visualizations = [item for item in child.get("visualizations", [])
                                      if isinstance(item, dict) and item.get("id")]
                    if not visualizations:
                        continue
                    message["visualizations"] = visualizations
                    message["attachments"] = child.get("attachments") or []
                    run["visualizations"] = visualizations
                    self.store.put("agent_run", owner, run)
                    changed = True
                except (httpx.HTTPError, ValueError):
                    continue
        if changed:
            self.store.put("conversation", owner, conversation, action="unified_agent.visualizations.backfilled")
        return conversation

    def _save(self, run: dict):
        run["elapsed_ms"] = round((time.time() - run["started_epoch"]) * 1000)
        self.store.put("agent_run", run["owner"], run)

    def _step(self, run: dict, key: str, title: str, status: str, detail: str, started: float):
        run["steps"].append({
            "key": key, "title": title, "status": status, "detail": detail,
            "started_at": now(), "ended_at": now(), "duration_ms": round((time.monotonic() - started) * 1000),
        })
        self._save(run)

    def _cluster_recall(self, owner: str, query: str) -> dict:
        started = time.monotonic()
        snapshot = self.ingest.topology(owner)
        topology_version = snapshot.get("_topology_version")
        cached = self._cluster_projection_cache.get(owner)
        if cached and cached[0] == topology_version:
            clusters, links_by_source = cached[1], cached[2]
        else:
            clusters = list(snapshot.get("knowledge_clusters", []))
            links_by_source = {}
            for link in snapshot.get("cross_brain_links", []):
                links_by_source.setdefault(str(link.get("source_ref") or ""), []).append(link)
            self._cluster_projection_cache[owner] = (topology_version, clusters, links_by_source)
        query_text = (query or "").casefold()
        candidates = []
        for cluster in clusters:
            links = links_by_source.get(f"knowledge://cluster/{cluster.get('cluster_id')}", [])
            labels = [str(link.get("anchor_value") or "") for link in links]
            score = sum(1 for label in labels if label and any(
                part and part.casefold() in query_text for part in re.split(r"[/\s]+", label)
            ))
            if score:
                candidates.append({
                    "cluster_id": cluster.get("cluster_id"), "tree_id": cluster.get("tree_id"),
                    "occurrence_count": cluster.get("occurrence_count", 0),
                    "independent_source_count": cluster.get("independent_source_count", 0),
                    "generation": cluster.get("generation"), "anchors": labels[:6], "score": score,
                })
        candidates.sort(key=lambda item: (-item["score"], -item["occurrence_count"], str(item["cluster_id"])))
        temporal_plan = infer_query_plan(query or "")
        temporal_hits = []
        if temporal_plan.has_content:
            for cluster in snapshot.get("knowledge_temporal_clusters", []):
                if not cluster.get("start") or not cluster.get("end"):
                    continue
                if cluster["end"] < (temporal_plan.content_start or "") or cluster["start"] > (temporal_plan.content_end or ""):
                    continue
                temporal_hits.append({
                    "temporal_cluster_id": cluster.get("temporal_cluster_id"),
                    "tree_id": cluster.get("tree_id"),
                    "bucket": cluster.get("bucket"),
                    "member_count": cluster.get("member_count", 0),
                    "document_count": cluster.get("document_count", 0),
                    "score": 1,
                })
        return {
            "status": "succeeded" if candidates or temporal_hits else "no_hit",
            "version": "knowledge-cluster-and-temporal-v2",
            "hits": candidates[:5],
            "hit_count": len(candidates),
            "temporal_hits": temporal_hits[:8],
            "temporal_hit_count": len(temporal_hits),
            "temporal_plan": {
                "mode": temporal_plan.mode,
                "content_start": temporal_plan.content_start,
                "content_end": temporal_plan.content_end,
                "ingest_start": temporal_plan.ingest_start,
                "ingest_end": temporal_plan.ingest_end,
            },
            "duration_ms": round((time.monotonic() - started) * 1000),
            "note": "星团只提供成员引用线索，最终结论仍由目标脑独立核验。",
        }

    async def _invoke_model(self, model: dict, query: str, context: dict, history: list[dict], *, role: str,
                            on_delta=None, answer_mode: str = "standard") -> tuple[str, dict, int, dict]:
        fast = answer_mode == "fast"
        if fast:
            messages = [{"role": "system", "content": fast_system_prompt(query)
                         + f"\n你当前只负责{role}，不得冒充其他认知脑。"}]
            messages.extend({"role": item["role"], "content": str(item.get("content", ""))[:800]}
                            for item in history[-4:] if item.get("role") in {"user", "assistant"})
            messages.append({"role": "user", "content": fast_evidence_prompt(query, context)})
            temperature, max_tokens = 0, 2000
        else:
            messages = [{"role": "system", "content": SYSTEM_PROMPT + f"\n你当前只负责{role}，不得冒充其他认知脑。"}]
            messages.extend({"role": item["role"], "content": str(item.get("content", ""))[:2000]}
                            for item in history[-6:] if item.get("role") in {"user", "assistant"})
            messages.append({"role": "user", "content": query + "\n\n本次授权证据：\n" + json.dumps(context, ensure_ascii=False)})
            temperature, max_tokens = 0.2, None
        headers = {"Authorization": "Bearer " + self.store.reveal(model["secret_ref"])} if model.get("secret_ref") else {}
        started = time.monotonic()
        # Operators raise this on the model page; ignoring it cut full two-lane
        # answers off mid-generation on slower reasoning deployments.
        timeout_seconds = min(max(int(model.get("timeout") or 90), 5), 300)
        supports_stream = bool(model.get("supports_stream", True))
        attempt = 0
        status_code = 502
        stream_first_token: float | None = None
        stream_finished: float | None = None
        async with self.client(timeout_seconds) as connection:
            for stream in ([True, False] if supports_stream else [False]):
                attempt += 1
                # The retry gets its own window: it exists precisely because the
                # previous attempt returned nothing usable.
                status_code, payload, stream_first_token, stream_finished = await self._provider_exchange(
                    connection, model, headers, messages, stream=stream,
                    on_delta=on_delta if stream else None, deadline=time.monotonic() + timeout_seconds,
                    temperature=temperature, max_tokens=max_tokens)
                # The gateway sometimes ends an SSE stream right after the role
                # frame, which used to fail the whole answer with a 502.  Retry
                # once without streaming so the run still produces an answer.
                if status_code != 200 or (payload.get("choices") or [{}])[0].get("message", {}).get("content"):
                    break
        duration = round((time.monotonic() - started) * 1000)
        if status_code != 200:
            raise HTTPException(status_code, inference_error(status_code, model["model"], payload))
        try:
            answer = payload["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise HTTPException(502, "模型返回内容为空或协议不兼容")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        telemetry = {
            "provider_roundtrip_ms": duration,
            "provider_attempts": attempt,
            "model_timeout_seconds": timeout_seconds,
            "ttft_ms": round((stream_first_token - started) * 1000) if stream_first_token else None,
            "generation_duration_ms": round((stream_finished - stream_first_token) * 1000)
            if stream_finished and stream_first_token else None,
            "streaming": bool(stream_first_token),
        }
        return answer.strip(), {key: value for key, value in usage.items()
                               if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)}, duration, telemetry

    async def _provider_exchange(self, connection, model: dict, headers: dict, messages: list[dict], *,
                                 stream: bool, on_delta, deadline: float,
                                 temperature: float = 0.2, max_tokens: int | None = None) -> tuple[int, dict, float | None, float | None]:
        """One chat round-trip; returns status, OpenAI-shaped payload, first token, finish."""
        request_payload = {"model": model["model"], "messages": messages, "temperature": temperature}
        if max_tokens:
            request_payload["max_tokens"] = max_tokens
        if not stream:
            response = await connection.post(model["base_url"] + "/chat/completions",
                                             headers=headers, json=request_payload)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            return response.status_code, payload, None, None
        request_payload["stream"] = True
        async with connection.stream("POST", model["base_url"] + "/chat/completions",
                                     headers=headers, json=request_payload) as response:
            if "text/event-stream" not in response.headers.get("content-type", "").lower():
                # Some OpenAI-compatible gateways ignore stream=true and return a
                # normal JSON body; read it instead of losing the answer.
                raw = await response.aread()
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, ValueError):
                    payload = {}
                return response.status_code, payload, None, None
            parts: list[str] = []
            usage: dict = {}
            first: float | None = None
            async for line in response.aiter_lines():
                if time.monotonic() >= deadline:
                    raise httpx.TimeoutException("model wall-clock timeout", request=response.request)
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                choices = event.get("choices") or []
                if choices:
                    delta = (choices[0].get("delta") or {}).get("content")
                    if isinstance(delta, str) and delta:
                        if first is None:
                            first = time.monotonic()
                        parts.append(delta)
                        if on_delta is not None:
                            on_delta("".join(parts))
            return (response.status_code,
                    {"choices": [{"message": {"content": "".join(parts)}}], "usage": usage},
                    first, time.monotonic())

    async def _semantic_brain(self, owner: str, brain: str, body, history: list[dict],
                              candidate_cluster_ids: list[str] | None = None,
                              deployment: str = "", emit=None) -> dict:
        started = time.monotonic()
        answer_mode = getattr(body, "answer_mode", "standard")
        model = self._active_model(brain, deployment=deployment)
        base = {
            "brain": brain, "label": BRAIN_LABELS[brain], "status": "running", "started_at": now(),
            "model_deployment_id": model.get("id") if model else None,
            "model_deployment": model.get("deployment") if model else None,
            "model": model.get("model") if model else None, "generation": None,
            "trace_id": None, "evidence_count": 0, "answer": "", "answer_basis": "evidence", "error": None,
            "answer_mode": answer_mode,
        }
        if not model:
            return {**base, "status": "unavailable", "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": _safe_error("MODEL_NOT_CONFIGURED", f"{BRAIN_LABELS[brain]}尚未启用对话模型")}
        retrieval_started: float | None = None
        model_started: float | None = None
        retrieval_duration: int | None = None

        def failure_timings() -> dict:
            # A model failure must not be charged to retrieval; the latency fields
            # are what the run panel and the acceptance manual read.
            return {
                "retrieval_duration_ms": retrieval_duration if retrieval_duration is not None else (
                    round((time.monotonic() - retrieval_started) * 1000) if retrieval_started else None),
                "provider_roundtrip_ms": round((time.monotonic() - model_started) * 1000) if model_started else None,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }

        try:
            request = body.model_dump(exclude={"route_mode", "requested_brains", "model_deployment", "answer_mode"})
            if brain == "knowledge" and candidate_cluster_ids:
                request["candidate_cluster_ids"] = candidate_cluster_ids[:256]
            retrieval_started = time.monotonic()
            result = await run_in_threadpool(self.ingest.search, owner, brain, request)
            retrieval_duration = round((time.monotonic() - retrieval_started) * 1000)
            raw = result.get("result") or {}
            base["trace_id"] = result.get("trace_id") or raw.get("trace_id")
            base["evidence_count"] = len(raw.get("evidence") or [])
            if not raw.get("claims") and brain != "knowledge":
                return {**base, "status": "no_hit", "evidence": result, "ended_at": now(),
                        "retrieval_duration_ms": retrieval_duration,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "answer": "当前授权范围内没有找到可引用证据。"}
            # A knowledge question with no corpus evidence is not a reason to
            # stop half-way: the model still answers its general-knowledge lane,
            # which the prompt requires to be labelled as non-project material.
            if raw.get("claims"):
                context = (evidence_context(result, limit=9000, top_k=8, text_limit=700)
                           if answer_mode == "fast" else evidence_context(result))
            else:
                context = {"status": "no_evidence", "warnings": [], "evidence": [],
                           "selected_count": 0, "available_count": 0}
            model_started = time.monotonic()
            answer, usage, model_duration, model_telemetry = await self._invoke_model(
                model, body.query, context, history, role=BRAIN_LABELS[brain], on_delta=emit,
                answer_mode=answer_mode,
            )
            if brain == "knowledge":
                answer = (fast_reference_note(body.query, answer, context) if answer_mode == "fast"
                          else medical_reference_note(body.query, answer, context))
            route = result.get("route") or {}
            tree_ids = []
            node_ids = list(route.get("selected") or []) if brain == "knowledge" else []
            if brain == "knowledge":
                workspace = await run_in_threadpool(self.ingest.workspace, owner)
                tree_ids = sorted({workspace.knowledge.brain.store.unit_tree.get(node_id)
                                   for node_id in node_ids if workspace.knowledge.brain.store.unit_tree.get(node_id)})
                generations = []
                for tree_id in tree_ids:
                    history_items = workspace.knowledge.brain.store.generations.history(tree_id)
                    if history_items:
                        generations.append(workspace.knowledge.brain.store.generations.current(tree_id).generation)
                base["generation"] = max(generations) if generations else None
            return {**base, "status": "succeeded", "answer": answer, "usage": usage,
                    "answer_basis": "evidence" if raw.get("claims") else "general",
                    "retrieval_duration_ms": retrieval_duration,
                    "model_duration_ms": model_duration, "provider_roundtrip_ms": model_duration,
                    "ttft_ms": model_telemetry.get("ttft_ms"),
                    "generation_duration_ms": model_telemetry.get("generation_duration_ms"),
                    "streaming": model_telemetry.get("streaming", False),
                    "provider_attempts": model_telemetry.get("provider_attempts", 1),
                    "evidence": result, "tree_ids": tree_ids,
                    "node_ids": node_ids, "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000)}
        except HTTPException as exc:
            return {**base, "status": "failed", "ended_at": now(), **failure_timings(),
                    "error": _safe_error(f"MODEL_HTTP_{exc.status_code}", str(exc.detail))}
        except KnowledgeWorkspaceWarmingError as exc:
            return {**base, "status": "warming", "ended_at": now(), **failure_timings(),
                    "error": _safe_error("KNOWLEDGE_INDEX_WARMING", str(exc))}
        except httpx.TimeoutException:
            return {**base, "status": "failed", "evidence": result if 'result' in locals() else None,
                    "ended_at": now(), **failure_timings(),
                    "error": _safe_error("MODEL_TIMEOUT", f"{BRAIN_LABELS[brain]}模型调用超时")}
        except asyncio.CancelledError:
            # ``pause`` has already written the durable terminal state.  Do
            # not append a synthetic failed assistant answer for a deliberate
            # user cancellation.
            persisted = self.store.get("agent_run", owner, run["id"])
            if not persisted or persisted.get("status") != "paused":
                run.update(status="failed", overall_status="failed", ended_at=now(),
                           error=_safe_error("INTERRUPTED", "统一编排已中断；已完成步骤仍可回看。"))
                self._save(run)
            raise
        except Exception:
            return {**base, "status": "failed", "ended_at": now(), **failure_timings(),
                    "error": _safe_error("BRAIN_EXECUTION_FAILED", f"{BRAIN_LABELS[brain]}执行失败；请按运行编号检查日志")}

    async def _structured_brain(self, owner: str, body, skill_config: dict | None = None,
                                deployment: str = "") -> dict:
        started = time.monotonic()
        model = self._active_model("structured", deployment=deployment)
        base = {"brain": "structured", "label": BRAIN_LABELS["structured"], "status": "running",
                "started_at": now(), "model_deployment_id": model.get("id") if model else None,
                "model_deployment": model.get("deployment") if model else None,
                "model": model.get("model") if model else None, "generation": None,
                "trace_id": None, "evidence_count": 0, "answer": "", "error": None}
        if not model:
            return {**base, "status": "unavailable", "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": _safe_error("MODEL_NOT_CONFIGURED", "结构化数据推理脑尚未启用对话模型")}
        token = os.environ.get("ENGINE_SERVICE_TOKEN", "")
        if not token:
            return {**base, "status": "unavailable", "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": _safe_error("AGENT_ADAPTER_UNAVAILABLE", "结构化 Agent 服务凭据未配置")}
        headers = {"X-Service-Token": token}
        try:
            async with self.client(185) as connection:
                created = await connection.post(self.agent_url + "/api/v1/agent/sessions", headers=headers,
                                                json={"title": ("统一 · " + body.query[:50])})
                created.raise_for_status()
                session = created.json()
                self.store.put("structured_session", owner, {"id": session["id"], "unified": True})
                if skill_config and skill_config.get("mode") in {"auto", "manual"}:
                    skill_response = await connection.post(
                        self.agent_url + f"/api/v1/agent/sessions/{session['id']}/skills",
                        headers=headers,
                        json={"mode": skill_config["mode"], "skills": skill_config.get("skills", [])},
                    )
                    skill_response.raise_for_status()
                sent = await connection.post(self.agent_url + f"/api/v1/agent/sessions/{session['id']}/messages",
                                             headers=headers, json={"message": body.query, "model": model["id"], "attachments": []})
                sent.raise_for_status()
                child = sent.json()
                self.store.put("structured_run", owner, {"id": child["id"], "session_id": session["id"], "unified": True})
                deadline = time.monotonic() + min(180, model.get("timeout", 180))
                while child.get("status") == "running" and time.monotonic() < deadline:
                    await asyncio.sleep(0.25)
                    response = await connection.get(self.agent_url + f"/api/v1/agent/runs/{child['id']}", headers=headers)
                    response.raise_for_status()
                    child = response.json()
                if child.get("status") == "running":
                    raise httpx.TimeoutException("structured agent timeout")
            if child.get("status") != "completed":
                error = child.get("error") or {}
                return {**base, "status": "failed", "child_session_id": session["id"], "child_run_id": child["id"],
                        "ended_at": now(), "duration_ms": round((time.monotonic() - started) * 1000),
                        "error": _safe_error(error.get("code", "STRUCTURED_AGENT_FAILED"),
                                             error.get("message", "结构化 Agent 执行失败"))}
            views = child.get("dataset_views") or []
            generations = [view.get("route", {}).get("generation") for view in views if isinstance(view, dict)]
            return {**base, "status": "succeeded", "answer": child.get("answer", ""),
                    "usage": child.get("usage") or {}, "dataset_views": views,
                    "visualizations": child.get("visualizations") or [],
                    "attachments": child.get("attachments") or [],
                    "execution_mode": child.get("execution_mode"), "skill": child.get("skill"),
                    "generation": next((item for item in generations if item is not None), None),
                    "trace_id": child.get("id"), "evidence_count": len(views),
                    "child_session_id": session["id"], "child_run_id": child["id"],
                    "ended_at": now(), "duration_ms": round((time.monotonic() - started) * 1000)}
        except httpx.TimeoutException:
            return {**base, "status": "failed", "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": _safe_error("STRUCTURED_TIMEOUT", "结构化 Agent 执行超时")}
        except httpx.HTTPError:
            return {**base, "status": "unavailable", "ended_at": now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": _safe_error("STRUCTURED_AGENT_UNAVAILABLE", "结构化 Agent 服务不可达")}

    async def _merge(self, query: str, brain_runs: dict[str, dict], history: list[dict],
                     deployment: str = "", answer_mode: str = "standard") -> tuple[str, dict]:
        usable = {brain: value for brain, value in brain_runs.items() if value.get("status") == "succeeded"}
        if not usable:
            details = [f"- {value['label']}：{value.get('error', {}).get('message') or '无可用证据'}"
                       for value in brain_runs.values() if value.get("status") != "skipped"]
            return "本次没有获得可用于正式回答的脑结果。\n\n" + "\n".join(details), {
                "status": "skipped", "model_deployment_id": None, "model_deployment": None,
                "model": None, "duration_ms": 0,
                "limitations": ["星团快速线索不能替代目标脑的正式核验结果。"],
            }
        model = self._active_model("orchestrator", deployment=deployment)
        source = {brain: {"answer": value.get("answer"), "status": value.get("status"),
                          "evidence_count": value.get("evidence_count"), "trace_id": value.get("trace_id")}
                  for brain, value in usable.items()}
        if len(usable) == 1:
            # One brain has nothing to synthesise.  A rewrite pass cost a full
            # second model round-trip (13.9s measured) and only truncated and
            # re-labelled the brain's own answer.
            value = next(iter(usable.values()))
            return value["answer"], {"status": "template", "model_deployment_id": None,
                                     "model_deployment": None, "model": None, "duration_ms": 0,
                                     "inputs": list(source),
                                     "limitations": [f"仅 {value['label']} 产出可用结果，未做二次改写；答案与该脑结论逐字一致。"]}
        if answer_mode == "fast" or not model:
            answer = "\n\n".join(f"### {value['label']}\n\n{value['answer']}" for value in usable.values())
            limitation = ("快速档按各脑结论并列返回，未做二次改写。" if answer_mode == "fast"
                          else "未配置统一编排模型；当前按脑并列呈现，未改写各脑结论。")
            return answer, {"status": "template", "model_deployment_id": None, "model_deployment": None,
                            "model": None, "duration_ms": 0, "inputs": list(source),
                            "limitations": [limitation]}
        context = {"status": "ok", "warnings": [], "evidence": [
            {"evidence_id": f"brain-{brain}", "ref": f"brain-run://{value.get('trace_id') or brain}",
             "detail": value["label"], "text": value.get("answer", "")[:5000], "excerpt": len(value.get("answer", "")) > 5000}
            for brain, value in usable.items()
        ], "selected_count": len(usable), "available_count": len(usable)}
        try:
            answer, usage, duration, telemetry = await self._invoke_model(
                model, query, context, history, role="统一认知控制面；只合并并标明各脑结果"
            )
            return answer, {"status": "succeeded", "model_deployment_id": model["id"],
                            "model_deployment": model.get("deployment"), "model": model["model"],
                            "duration_ms": duration, "provider_roundtrip_ms": duration,
                            "ttft_ms": telemetry.get("ttft_ms"),
                            "generation_duration_ms": telemetry.get("generation_duration_ms"),
                            "usage": usage, "inputs": list(source)}
        except (HTTPException, httpx.HTTPError):
            answer = "\n\n".join(f"### {value['label']}\n\n{value['answer']}" for value in usable.values())
            return answer, {"status": "failed", "model_deployment_id": model["id"],
                            "model_deployment": model.get("deployment"), "model": model["model"],
                            "duration_ms": 0, "inputs": list(source),
                            "limitations": ["统一编排模型调用失败；已保留并列的各脑核验结果，未进行二次改写。"]}

    async def execute(self, owner: str, session_id: str, body) -> dict:
        conversation = self.store.get("conversation", owner, session_id)
        if not conversation:
            raise HTTPException(404, "记录不存在或无访问权限")
        if conversation.get("brain") != "unified":
            raise HTTPException(400, "只有统一会话可以执行 3+1 编排")
        if conversation.get("archived"):
            raise HTTPException(409, "归档会话为只读，请先恢复后再发送消息")
        prior = self.store.get("agent_run", owner, conversation.get("last_run_id", ""))
        if prior and prior.get("status") == "running":
            raise HTTPException(409, "当前会话仍在执行，请等待完成后再发送")
        selected, reasons = route_query(body.query, body.route_mode, body.requested_brains)
        answer_mode = getattr(body, "answer_mode", "standard")
        requested_deployment = (body.model_deployment or "").strip().lower()
        if requested_deployment and not any(row.get("deployment") == requested_deployment for row in self._chat_models()):
            raise HTTPException(400, "所选模型部署未启用或不存在，请刷新模型选项后重试")
        deployment = requested_deployment or self._default_deployment()
        run = {
            "id": uid("run"), "owner": owner, "session_id": session_id, "brain": "unified",
            "model": "3+1-auto", "model_deployment": deployment,
            "model_deployment_requested": requested_deployment,
            "answer_mode": answer_mode,
            "status": "running", "started_at": now(), "started_epoch": time.time(),
            "steps": [], "route_mode": body.route_mode, "requested_brains": body.requested_brains,
            "compiled_scope": {key: getattr(body, key) for key in ("start", "end", "place", "person") if getattr(body, key)},
            "route_plan": {"selected": selected, "reasons": reasons},
            "cluster_run": {"status": "pending", "hits": []},
            "brain_runs": {brain: {"brain": brain, "label": BRAIN_LABELS[brain], "status": "selected" if brain in selected else "skipped",
                                      "route_reason": reasons.get(brain, "问题与该脑能力不匹配"), "answer": "", "error": None}
                           for brain in BRAINS},
        }
        self._save(run)
        history = [item for item in conversation.get("messages", []) if item.get("status") != "failed"][-6:]
        conversation["last_run_id"] = run["id"]
        conversation["model_deployment"] = deployment or None
        skill_config = {
            "mode": conversation.get("skill_mode", "auto"),
            "skills": conversation.get("selected_skills", []),
        }
        conversation.setdefault("messages", []).append({"id": uid("message"), "role": "user", "content": body.query,
                                                         "run_id": run["id"], "created_at": now()})
        if conversation.get("title") == "新会话":
            conversation["title"] = body.query[:40]
        self.store.put("conversation", owner, conversation, action="unified_agent.started")
        try:
            started = time.monotonic()
            self._step(run, "compile", "输入理解与基座编译", "succeeded",
                       f"自动路由选择 {len(selected)} 个脑；时间和实体条件分别传入目标脑", started)
            started = time.monotonic()
            run["cluster_run"] = await run_in_threadpool(self._cluster_recall, owner, body.query)
            self._step(run, "cluster", "记忆星团快速检索", run["cluster_run"]["status"],
                       f"命中 {run['cluster_run']['hit_count']} 个可追溯星团；仅作为快速线索", started)
            started = time.monotonic()
            # One routed brain is answered verbatim by _merge, so its streamed
            # tokens already are the final answer.  A multi-brain run has no
            # single owner of the answer, so it keeps the plain "still running"
            # feedback instead of showing a partial brain result.
            stream_brain = selected[0] if len(selected) == 1 and selected[0] != "structured" else None
            last_saved = time.monotonic() - 1.0

            def emit(text: str) -> None:
                nonlocal last_saved
                if time.monotonic() - last_saved < 0.8:
                    return
                last_saved = time.monotonic()
                # pause() writes the durable terminal state from another task;
                # never overwrite it with the in-flight "running" record.
                if (self.store.get("agent_run", owner, run["id"]) or {}).get("status") != "running":
                    return
                run["live_answer"] = text
                self._save(run)

            tasks = []
            task_brains = []
            for brain in selected:
                task_brains.append(brain)
                tasks.append(self._structured_brain(owner, body, skill_config, deployment=deployment)
                             if brain == "structured" else self._semantic_brain(
                                 owner,
                                 brain,
                                 body,
                                 history,
                                 [hit.get("cluster_id") for hit in run["cluster_run"].get("hits", [])
                                  if hit.get("cluster_id")] if brain == "knowledge" else None,
                                 deployment=deployment,
                                 emit=emit if brain == stream_brain else None,
                             ))
            results = await asyncio.gather(*tasks)
            for brain, result in zip(task_brains, results):
                result["route_reason"] = reasons[brain]
                run["brain_runs"][brain] = result
                self._save(run)
            status_counts = Counter(value["status"] for value in run["brain_runs"].values())
            self._step(run, "verify", "三脑独立并行核验", "succeeded" if status_counts["succeeded"] else "partial",
                       "；".join(f"{BRAIN_LABELS[b]}={run['brain_runs'][b]['status']}" for b in BRAINS), started)
            started = time.monotonic()
            answer, merge_run = await self._merge(body.query, run["brain_runs"], history,
                                                  deployment=deployment, answer_mode=answer_mode)
            run["merge_run"] = merge_run
            run["live_answer"] = answer
            failed = sum(status_counts[key] for key in ("failed", "unavailable", "denied"))
            succeeded = status_counts["succeeded"]
            if succeeded and failed:
                overall = "partial"
            elif succeeded:
                overall = "succeeded"
            elif status_counts["no_hit"]:
                overall = "no_hit"
            else:
                overall = "failed"
            self._step(run, "merge", "多脑证据合一", "succeeded" if succeeded else "partial",
                       "使用编排模型合并" if merge_run["status"] == "succeeded"
                       else merge_run.get("limitations", ["未改写各脑结论"])[0], started)
            evidence_manifest = {
                brain: {"trace_id": value.get("trace_id"), "count": value.get("evidence_count", 0),
                        "tree_ids": value.get("tree_ids", []), "node_ids": value.get("node_ids", [])}
                for brain, value in run["brain_runs"].items() if value.get("status") != "skipped"
            }
            visualizations = [item for item in run["brain_runs"].get("structured", {}).get("visualizations", [])
                              if isinstance(item, dict) and item.get("id")]
            run["visualizations"] = visualizations
            run.update(status=overall, overall_status=overall, evidence_manifest=evidence_manifest, ended_at=now())
            conversation = self.store.get("conversation", owner, session_id)
            conversation["messages"].append({
                "id": uid("message"), "role": "assistant", "content": answer, "run_id": run["id"],
                "model_deployment": deployment or None, "answer_mode": answer_mode,
                "status": overall, "feedback": None, "cluster_run": run["cluster_run"],
                "brain_runs": run["brain_runs"], "merge_run": merge_run,
                "visualizations": visualizations,
                "attachments": run["brain_runs"].get("structured", {}).get("attachments", []),
                "evidence_manifest": evidence_manifest, "created_at": now(),
            })
            self.store.put("conversation", owner, conversation)
            self._save(run)
            self.store.audit(owner, "unified_agent.completed", session_id, {
                "run_id": run["id"], "status": overall, "selected_brains": selected,
                "model_deployment": deployment, "answer_mode": answer_mode,
                "brain_statuses": {brain: value["status"] for brain, value in run["brain_runs"].items()},
            })
        except Exception:
            run.update(status="failed", overall_status="failed", ended_at=now(),
                       error=_safe_error("UNIFIED_AGENT_FAILED", "统一编排未完成；已完成步骤和运行编号已保留"))
            self._save(run)
            conversation = self.store.get("conversation", owner, session_id)
            conversation["messages"].append({"id": uid("message"), "role": "assistant",
                                             "content": run["error"]["message"], "run_id": run["id"],
                                             "status": "failed", "feedback": None, "created_at": now()})
            self.store.put("conversation", owner, conversation)
            self.store.audit(owner, "unified_agent.failed", session_id, {"run_id": run["id"]})
        result = self.store.get("conversation", owner, session_id)
        result["run"] = self.store.get("agent_run", owner, run["id"])
        return result
