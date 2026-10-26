import json

import httpx
from fastapi.testclient import TestClient

from platform_server.tests.test_platform import (
    configure_model,
    create_app,
    create_source,
    identity_server,
    login,
    wait_job,
)
from platform_server.agent_runs import (
    evidence_context,
    fast_evidence_prompt,
    fast_reference_note,
    fast_system_prompt,
)
from platform_server.unified_agent import route_query


def test_route_query_is_deterministic_and_scoped():
    selected, reasons = route_query("查询各月销售金额并概括最高月份")
    assert selected == ["structured"]
    assert "销售" in reasons["structured"]

    selected, _ = route_query("回忆上次拍摄的视频里发生了什么")
    assert selected == ["memory"]

    selected, _ = route_query("请综合判断这个问题")
    assert selected == ["memory", "knowledge", "structured"]

    selected, reasons = route_query("任意问题", "scoped", ["knowledge", "structured"])
    assert selected == ["knowledge", "structured"]
    assert all("用户明确限定" in value for value in reasons.values())


def test_unified_knowledge_run_keeps_3_plus_1_trace(tmp_path):
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "依据果园资料，橙子应在秋季采收。[brain-knowledge]"}}],
            "usage": {"total_tokens": 27},
        })

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source,
            "title": "果园采收规范",
            "content": "果园中的橙子应在秋季采收，并保持存放环境通风。",
        }).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        configure_model(client, "knowledge", "chat")

        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        legacy = client.post(f"/api/v1/platform/sessions/{session['id']}/messages", json={
            "query": "果园橙子应该如何采收？", "model_id": "not-used",
        })
        assert legacy.status_code == 400

        response = client.post(
            f"/api/v1/platform/unified/sessions/{session['id']}/messages",
            json={"query": "果园橙子应该如何采收？"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        run = result["run"]
        assert run["brain"] == "unified"
        assert run["route_plan"]["selected"] == ["knowledge"]
        assert [step["key"] for step in run["steps"]] == ["compile", "cluster", "verify", "merge"]
        assert run["brain_runs"]["knowledge"]["status"] == "succeeded"
        assert run["brain_runs"]["memory"]["status"] == "skipped"
        assert run["brain_runs"]["structured"]["status"] == "skipped"
        assert run["cluster_run"]["status"] in {"succeeded", "no_hit"}
        assert run["merge_run"]["status"] == "template"
        assert result["messages"][-1]["brain_runs"]["knowledge"]["evidence_count"] > 0
        assert requests and "本次授权证据" in requests[0]["messages"][-1]["content"]


def test_chat_deployments_allow_multiple_models_and_switch_per_run(tmp_path):
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "依据果园资料，橙子应在秋季采收。"}}],
            "usage": {"total_tokens": 12},
        })

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source,
            "title": "果园采收规范",
            "content": "果园中的橙子应在秋季采收，并保持存放环境通风。",
        }).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"

        def model(deployment, name, model_id, is_default):
            response = client.put("/api/v1/platform/models/new", json={
                "name": name, "brain": "knowledge", "purpose": "chat",
                "model": model_id, "base_url": "http://model/v1", "api_key": "provider-secret",
                "enabled": True, "deployment": deployment, "deployment_name": deployment.title(),
                "is_default": is_default,
            })
            assert response.status_code == 200, response.text
            return response.json()

        first_codex = model("codex", "Codex A", "codex-model", True)
        deepseek = model("deepseek", "DeepSeek", "deepseek-model", False)
        second_codex = model("codex", "Codex B", "codex-model", True)
        rows = {row["id"]: row for row in client.get("/api/v1/platform/models").json()["items"]}
        assert rows[first_codex["id"]]["enabled"] and rows[deepseek["id"]]["enabled"]
        assert rows[second_codex["id"]]["is_default"] and not rows[first_codex["id"]]["is_default"]

        options = client.get("/api/v1/platform/model-deployments").json()
        assert options["default"] == "codex"
        assert {item["id"] for item in options["items"]} == {"codex", "deepseek"}
        assert sum(item["default"] for item in options["items"]) == 1

        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        switched = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages", json={
            "query": "果园橙子应该如何采收？", "route_mode": "scoped", "requested_brains": ["knowledge"],
            "model_deployment": "deepseek",
        })
        assert switched.status_code == 200, switched.text
        assert switched.json()["run"]["model_deployment"] == "deepseek"
        assert switched.json()["run"]["brain_runs"]["knowledge"]["model_deployment"] == "deepseek"
        assert requests[-1]["model"] == "deepseek-model"

        default_run = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages", json={
            "query": "果园橙子应该如何采收？", "route_mode": "scoped", "requested_brains": ["knowledge"],
        })
        assert default_run.status_code == 200, default_run.text
        assert default_run.json()["run"]["model_deployment"] == "codex"
        assert requests[-1]["model"] == "codex-model"


def test_unified_scoped_missing_model_is_visible_not_silently_rerouted(tmp_path):
    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        response = client.post(
            f"/api/v1/platform/unified/sessions/{session['id']}/messages",
            json={"query": "只检查知识资料", "route_mode": "scoped", "requested_brains": ["knowledge"]},
        )
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        assert run["route_plan"]["selected"] == ["knowledge"]
        assert run["brain_runs"]["knowledge"]["status"] == "unavailable"
        assert run["brain_runs"]["knowledge"]["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert run["status"] == "failed"


def test_unified_timeout_keeps_retrieved_evidence_for_later_inspection(tmp_path):
    def upstream(request):
        if request.url.host != 'model':
            return identity_server(request)
        raise httpx.ReadTimeout('provider timeout', request=request)

    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        login(client)
        source = create_source(client)
        job = client.post('/api/v1/platform/imports/text', json={
            'source_id': source, 'title': '高血压资料.txt', 'content': '高血压相关资料。',
        }).json()
        completed = wait_job(client, job['id'])
        assert completed['status'] in {'succeeded', 'pending_review'}
        if completed['status'] == 'pending_review':
            reviewed = client.post(f"/api/v1/platform/assets/{completed['asset_id']}/review", json={
                'action': 'approve', 'reason': '超时证据保留测试', 'expected_status': 'pending_review',
            })
            assert reviewed.status_code == 200, reviewed.text
        configure_model(client, 'knowledge', 'chat')
        session = client.post('/api/v1/platform/sessions', json={'brain':'unified'}).json()['id']

        response = client.post(f'/api/v1/platform/unified/sessions/{session}/messages', json={
            'query':'高血压是什么原理？', 'route_mode':'scoped', 'requested_brains':['knowledge'],
        })

        assert response.status_code == 200, response.text
        knowledge = response.json()['run']['brain_runs']['knowledge']
        assert knowledge['error']['code'] == 'MODEL_TIMEOUT'
        assert knowledge['evidence']['result']['evidence']
        assert knowledge['evidence']['result']['evidence'][0]['document_title'] == '高血压资料.txt'


def test_viewer_cannot_override_unified_route_scope(tmp_path):
    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client, "reader")
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        response = client.post(
            f"/api/v1/platform/unified/sessions/{session['id']}/messages",
            json={"query": "只检查知识资料", "route_mode": "scoped", "requested_brains": ["knowledge"]},
        )
        assert response.status_code == 403


def test_unified_structured_chart_is_preserved_and_skills_are_session_scoped(tmp_path):
    forwarded_skill_contexts = []

    def upstream(request):
        path = request.url.path
        if request.url.host == "agent":
            if path == "/api/v1/agent/sessions" and request.method == "POST":
                return httpx.Response(200, json={"id": "child-session", "title": "统一销售"})
            if path.endswith("/skills") and request.method == "POST":
                return httpx.Response(200, json={"id": "child-session", "skill_mode": "auto", "selected_skills": []})
            if path.endswith("/messages") and request.method == "POST":
                return httpx.Response(200, json={
                    "id": "child-run", "status": "completed", "answer": "2025年销售趋势已生成。",
                    "dataset_views": [{"dataset": "sales", "route": {"generation": 3}}],
                    "visualizations": [{
                        "id": "chart-sales-2025", "title": "2025年各月销售情况",
                        "x_axis": {"name": "月份", "categories": ["1月", "2月"]},
                        "y_axis": {"name": "销售额", "unit": "元"},
                        "series": [{"name": "销售额", "data": [10, 20]}],
                        "source": {"datasets": ["sales"], "metrics": ["sale_amount"], "period": "2025"},
                    }],
                    "attachments": [], "usage": {"total_tokens": 2},
                })
            if path == "/api/v1/agent/runs/child-run" and request.method == "GET":
                # Completed run objects are process-local and disappear after an Agent restart.
                return httpx.Response(404)
            if path == "/api/v1/agent/sessions/child-session" and request.method == "GET":
                return httpx.Response(200, json={"id": "child-session", "messages": [{
                    "id": "assistant-message", "role": "assistant", "run_id": "child-run",
                    "visualizations": [{
                        "id": "chart-sales-2025", "title": "2025年各月销售情况",
                        "x_axis": {"name": "月份", "categories": ["1月", "2月"]},
                        "y_axis": {"name": "销售额", "unit": "元"},
                        "series": [{"name": "销售额", "data": [10, 20]}],
                        "source": {"datasets": ["sales"], "metrics": ["sale_amount"], "period": "2025"},
                    }],
                    "attachments": [],
                }]})
            if path == "/api/v1/agent/skills/analyze" and request.method == "POST":
                forwarded_skill_contexts.append(json.loads(request.content))
                return httpx.Response(200, json={
                    "draft": {"name": "monthly-sales", "display_name": "月度销售", "description": "月度销售趋势", "instructions": "生成月度趋势"},
                    "validation": {"valid": True, "errors": [], "warnings": []},
                })
            return httpx.Response(404)
        return identity_server(request)

    app = create_app(tmp_path, engine_url="http://engine", agent_url="http://agent", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        configure_model(client, "structured", "chat")
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        skills = client.post(f"/api/v1/agent/sessions/{session['id']}/skills", json={
            "mode": "manual", "skills": ["monthly-sales-trend"],
        })
        assert skills.status_code == 200, skills.text
        assert skills.json()["selected_skills"] == ["monthly-sales-trend"]
        response = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages", json={
            "query": "查询2025年各月销售情况，并给出折线图",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["messages"][-1]["visualizations"][0]["id"] == "chart-sales-2025"
        assert result["run"]["visualizations"][0]["title"] == "2025年各月销售情况"
        stored = app.state.store.get("conversation", "alice", session["id"])
        stored["messages"][-1].pop("visualizations")
        app.state.store.put("conversation", "alice", stored)
        stored_run = app.state.store.get("agent_run", "alice", result["run"]["id"])
        stored_run.pop("visualizations")
        app.state.store.put("agent_run", "alice", stored_run)
        refreshed = client.get(f"/api/v1/platform/sessions/{session['id']}")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["messages"][-1]["visualizations"][0]["id"] == "chart-sales-2025"
        draft = client.post("/api/v1/agent/skills/analyze", json={
            "idea": "沉淀月度销售分析", "model": "verified-model",
            "session_id": session["id"], "include_context": True,
        })
        assert draft.status_code == 200, draft.text
        assert forwarded_skill_contexts[0]["session_id"] is None
        assert "查询2025年各月销售情况" in forwarded_skill_contexts[0]["context"]
        assert "2025年销售趋势已生成" in forwarded_skill_contexts[0]["context"]


def test_single_usable_brain_is_returned_verbatim_without_an_orchestrator_rewrite(tmp_path):
    """One usable brain has nothing to synthesise; a rewrite pass only paid for a
    second round-trip and re-labelled the brain's own answer."""
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "资料证据：橙子应在秋季采收。"}}]})

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source, "title": "果园采收规范",
            "content": "果园中的橙子应在秋季采收，并保持存放环境通风。",
        }).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        configure_model(client, "knowledge", "chat")
        configure_model(client, "orchestrator", "chat")

        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        response = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages",
                               json={"query": "果园橙子应该如何采收？"})
        assert response.status_code == 200, response.text
        result = response.json()
        run = result["run"]
        assert run["route_plan"]["selected"] == ["knowledge"]
        assert len(requests) == 1
        assert run["merge_run"]["status"] == "template"
        assert run["merge_run"]["model"] is None
        assert "未做二次改写" in run["merge_run"]["limitations"][0]
        assert result["messages"][-1]["content"] == run["brain_runs"]["knowledge"]["answer"]
        assert run["steps"][-1]["detail"] == run["merge_run"]["limitations"][0]


def test_knowledge_question_without_evidence_still_answers_from_general_knowledge(tmp_path):
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content":
            "资料证据：本次授权资料没有相关记载。\n\n通识补充（非本项目证据）：久咳需先排除诱因。"}}]})

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source, "title": "果园采收规范",
            "content": "果园中的橙子应在秋季采收，并保持存放环境通风。",
        }).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        configure_model(client, "knowledge", "chat")
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        response = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages",
                               json={"query": "久咳不止怕冷应该怎么办？"})
        assert response.status_code == 200, response.text
        knowledge = response.json()["run"]["brain_runs"]["knowledge"]
        assert knowledge["status"] == "succeeded"
        assert knowledge["evidence_count"] == 0
        assert knowledge["answer_basis"] == "general"
        prompt = requests[0]["messages"][-1]["content"]
        assert '"status": "no_evidence"' in prompt
        assert '"selected_count": 0' in prompt


def test_truncated_sse_stream_retries_once_without_streaming(tmp_path):
    """The gateway can end an SSE stream after the role frame; that must not cost
    the whole answer, so one silent non-streaming retry is part of the contract."""
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                b'data: [DONE]\n\n'))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "资料证据：橙子应在秋季采收。\n\n通识补充（非本项目证据）：咳嗽需先排查诱因。"}}]})

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source, "title": "果园采收规范",
            "content": "果园中的橙子应在秋季采收，并保持存放环境通风。",
        }).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        configure_model(client, "knowledge", "chat")
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()
        response = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages",
                               json={"query": "果园橙子应该如何采收？"})
        assert response.status_code == 200, response.text
        result = response.json()
        knowledge = result["run"]["brain_runs"]["knowledge"]
        assert [item.get("stream") for item in requests] == [True, None]
        assert knowledge["status"] == "succeeded"
        assert knowledge["provider_attempts"] == 2
        assert knowledge["streaming"] is False
        assert "通识补充" in knowledge["answer"]
        assert result["messages"][-1]["content"] == knowledge["answer"]


def test_fast_prompt_and_reference_note_stay_compact_and_put_disclaimer_last():
    query = "久咳不止、怕冷，有相关方剂可以参考吗？"
    assert "方剂" in fast_system_prompt(query)
    assert "方剂" not in fast_system_prompt("果园橙子应该如何采收？")

    result = {"result": {
        "status": "ok", "warnings": [],
        "claims": [{"evidence_id": "e1", "value": "久咳" + "寒" * 2000}],
        "evidence": [{"evidence_id": "e1", "ref": "doc://1", "document_title": "医书",
                      "locator": {"page": 12}}],
    }}
    context = evidence_context(result, text_limit=700)
    assert len(context["evidence"][0]["text"]) == 700
    prompt = fast_evidence_prompt(query, context)
    assert prompt.startswith("问题：") and "[1] 医书" in prompt
    assert "本次授权证据" not in prompt

    answer = fast_reference_note(
        query, "依据资料，久咳需辨寒热。[1]\n\n以上为资料参考，具体请咨询专业医师。", context,
    )
    assert answer.count("以上为资料参考，具体请咨询专业医师。") == 1
    assert answer.endswith("以上为资料参考，具体请咨询专业医师。")
    assert "本次可追溯资料出处" in answer and "医书（第 12 页） [e1]" in answer


def test_fast_answer_mode_uses_compact_request_and_keeps_code_owned_sources(tmp_path):
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "依据资料，高血压患者应低盐饮食并规律测量血压。[1]"}}],
            "usage": {"total_tokens": 21}})

    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={
            "source_id": source, "title": "高血压管理规范",
            "content": "高血压患者应低盐饮食并规律测量血压，必要时遵医嘱用药。",
        }).json()
        completed = wait_job(client, job["id"])
        assert completed["status"] in {"succeeded", "pending_review"}
        if completed["status"] == "pending_review":
            reviewed = client.post(f"/api/v1/platform/assets/{completed['asset_id']}/review", json={
                "action": "approve", "reason": "快速档来源清单测试", "expected_status": "pending_review",
            })
            assert reviewed.status_code == 200, reviewed.text
        configure_model(client, "knowledge", "chat")
        session = client.post("/api/v1/platform/sessions", json={"brain": "unified"}).json()

        response = client.post(f"/api/v1/platform/unified/sessions/{session['id']}/messages", json={
            "query": "高血压应该如何治疗？", "route_mode": "scoped",
            "requested_brains": ["knowledge"], "answer_mode": "fast",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        run = result["run"]
        knowledge = run["brain_runs"]["knowledge"]
        assert run["answer_mode"] == "fast" and knowledge["answer_mode"] == "fast"
        payload = requests[0]
        assert payload["temperature"] == 0 and payload["max_tokens"] == 2000
        system = payload["messages"][0]["content"]
        assert "500-700 字" in system and "第二栏" not in system
        user_prompt = payload["messages"][-1]["content"]
        assert user_prompt.startswith("问题：") and "证据：" in user_prompt
        assert "本次授权证据" not in user_prompt
        assert "本次可追溯资料出处" in knowledge["answer"]
        assert knowledge["answer"].endswith("以上为资料参考，具体请咨询专业医师。")
        assert result["messages"][-1]["answer_mode"] == "fast"
