(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const state = { view: "overview", session: null, streamId: null, streamKind: "camera", poller: null };
  const titles = {
    overview: "系统总览",
    query: "统一认知台",
    knowledge: "知识认知脑",
    memory: "多模态时空记忆脑",
    structured: "结构化数据推理脑",
    topology: "拓扑、治理与审计",
  };
  const requestId = (prefix) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

  function showMessage(text = "", error = false) {
    const node = $("#global-message");
    node.textContent = text;
    node.className = error ? "global-message error-box" : "global-message";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    }[char]));
  }

  function pretty(value) { return JSON.stringify(value, null, 2); }
  function pill(value) { return `<span class="pill">${escapeHtml(value)}</span>`; }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let body = {};
    try { body = text ? JSON.parse(text) : {}; } catch { body = { error: { message: text || response.statusText } }; }
    if (!response.ok) {
      const error = body.error || body.detail || {};
      throw new Error(error.message || `请求失败（${response.status}）`);
    }
    return body;
  }

  async function toBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    const chunk = 0x8000;
    for (let index = 0; index < bytes.length; index += chunk) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
    }
    return btoa(binary);
  }

  async function loadSession() {
    try {
      const body = await api("/api/v1/console/session");
      state.session = body.session;
      $("#login-view").classList.add("hidden");
      $("#app-view").classList.remove("hidden");
      $("#user-subject").textContent = state.session.subject;
      $("#user-role").textContent = state.session.role;
      $("#side-mode").textContent = state.session.mode;
      await showView("overview");
    } catch {
      $("#login-view").classList.remove("hidden");
      $("#app-view").classList.add("hidden");
    }
  }

  async function showView(view) {
    state.view = view;
    $$(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
    $$("[data-page]").forEach((node) => node.classList.toggle("active-page", node.dataset.page === view));
    $("#page-title").textContent = titles[view];
    showMessage("");
    try {
      if (view === "overview") await refreshOverview();
      if (view === "knowledge") await refreshKnowledge();
      if (view === "memory") await refreshMemory();
      if (view === "structured") await refreshStructured();
      if (view === "topology") { await refreshTopology(); await refreshGovernance(); }
      if (view === "query") $("#query-text").focus();
    } catch (error) { showMessage(error.message, true); }
  }

  function renderBrainCards(overview) {
    const labels = {
      memory: "多模态时空记忆脑",
      knowledge: "知识认知脑（受管检索运行时）",
      structured: "结构化数据推理脑",
    };
    $("#brain-cards").innerHTML = Object.entries(overview.brains || {}).map(([key, info]) => `
      <article class="brain-card ${key}"><strong>${labels[key] || key}</strong><span class="status">${escapeHtml(info.status)}</span>
      <div class="meta">${Object.entries(info).filter(([name]) => name !== "status").map(([name, value]) => `<span>${escapeHtml(name)}：${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</span>`).join("")}</div></article>`).join("");
    $("#runtime-json").textContent = pretty({
      mode: overview.mode, fixtures_loaded: overview.fixtures_loaded, profile: overview.profile, runtime: overview.runtime,
    });
    const pending = overview.pending_review || [];
    $("#pending-list").innerHTML = pending.length
      ? pending.map((id) => `<div class="list-item"><strong>${escapeHtml(id)}</strong><div class="list-meta">待人工复核，不自动发布</div></div>`).join("")
      : `<div class="empty-state">当前没有待复核对象。</div>`;
    const audit = overview.recent_audit || [];
    $("#overview-audit").innerHTML = audit.length
      ? `<table class="audit-table"><thead><tr><th>请求</th><th>目标脑</th><th>状态</th><th>警告</th></tr></thead><tbody>${audit.map((item) => `<tr><td>${escapeHtml(item.request_id)}</td><td>${escapeHtml((item.targets || []).join(", "))}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml((item.warnings || []).join("；") || "-")}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty-state">还没有控制面审计；手工执行一次统一查询后会出现在这里。</div>`;
  }

  async function refreshOverview() { renderBrainCards(await api("/api/v1/console/overview")); }

  function renderQuery(result) {
    const stages = result.stages || {};
    const verify = stages.verification || {};
    const answer = stages.final_answer || {};
    const blocks = Object.values(verify.per_brain || {}).map((brain) => `
      <article class="result-brain ${escapeHtml(brain.status)}"><strong>${escapeHtml(brain.brain)} · ${escapeHtml(brain.status)}</strong>
      <div class="list-meta">${escapeHtml(brain.error?.message || (brain.warnings || []).join("；") || "已返回结果")}</div>
      ${(brain.claims || []).map((claim) => `<div class="claim"><b>${escapeHtml(claim.field)}</b>：${escapeHtml(claim.value)} <span class="list-meta">证据 ${escapeHtml(claim.evidence_id || "-")}</span></div>`).join("")}</article>`).join("");
    const fastItems = (stages.fast_recall?.items || []).map((item) => `<div class="claim"><b>${escapeHtml(item.source)}</b>：${escapeHtml(item.summary)} <span class="list-meta">证据 ${escapeHtml(item.evidence_id || "-")}</span></div>`).join("");
    $("#query-result").innerHTML = `
      <article class="stage-panel"><h3>阶段一 · 快速召回</h3>${fastItems || `<div class="empty-state">没有快速线索。</div>`}</article>
      <article class="stage-panel"><h3>阶段二 · 证据核验</h3>${blocks || `<div class="empty-state">没有脑返回核验结果。</div>`}<pre class="json-view">${escapeHtml(pretty(verify.conflicts || []))}</pre></article>
      <article class="stage-panel"><h3>阶段三 · 最终回答</h3><p>${escapeHtml(answer.answer || "未生成回答")}</p><div class="list-meta">${escapeHtml((answer.limitations || []).join("；") || (result.warnings || []).join("；") || "无额外警告")}</div><pre class="json-view">${escapeHtml(pretty({ evidence: result.evidence || [], audit: result.audit || {} }))}</pre></article>`;
  }

  $("#query-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const targets = $$(".target-check:checked").map((node) => node.value);
      if (!targets.length) throw new Error("请至少选择一个脑");
      const result = await api("/api/v1/console/query", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId("query"), trace_id: requestId("trace"), raw_query: $("#query-text").value,
          targets, constraints: { no_fabricated_precision: true, merge_mode: "separate" },
        }),
      });
      renderQuery(result);
      showMessage("查询完成；各脑结果、证据和限制已分别展开。");
    } catch (error) { showMessage(error.message, true); }
  });

  async function refreshKnowledge() {
    const body = await api("/api/v1/console/knowledge");
    $("#knowledge-runtime").textContent = pretty(body.runtime);
    $("#knowledge-count").textContent = body.documents.length;
    $("#knowledge-link-count").textContent = body.cross_brain_links.length;
    const docs = body.documents || [];
    $("#knowledge-documents").innerHTML = docs.length ? docs.map((item) => {
      const doc = item.document;
      const publication = item.publication || {};
      const pending = publication.status === "pending_review";
      return `<article class="list-item"><strong>${escapeHtml(doc.title || doc.document_id)} ${pill(publication.status || "-")}</strong>
        <div class="list-meta">${escapeHtml(doc.source_id || doc.source_object_ref || "-")} · 单元 ${item.unit_count || 0} · 文档 ${escapeHtml(doc.document_id)}</div>
        ${pending ? `<div class="item-actions"><button class="secondary knowledge-review" data-id="${escapeHtml(doc.document_id)}" data-action="approve">批准发布</button><button class="secondary knowledge-review" data-id="${escapeHtml(doc.document_id)}" data-action="reject">拒绝发布</button></div>` : ""}</article>`;
    }).join("") : `<div class="empty-state">尚无资料。使用上方文本或本地文件导入开始验收。</div>`;
  }

  $("#knowledge-import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/v1/console/knowledge/imports/text", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId("k-import"), trace_id: requestId("trace"), title: $("#knowledge-title").value || null,
          content: $("#knowledge-content").value, risk_domain: $("#knowledge-risk").value || null,
          source_id: "console_manual_import",
        }),
      });
      await refreshKnowledge();
      showMessage("文本资料已进入知识脑导入管道，可继续检索或复核。");
    } catch (error) { showMessage(error.message, true); }
  });

  $("#knowledge-file-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const file = $("#knowledge-file").files[0];
      if (!file) throw new Error("请选择本地文本文件");
      await api("/api/v1/console/knowledge/imports/files", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId("k-file"), trace_id: requestId("trace"), filename: file.name,
          media_kind: $("#knowledge-file-kind").value, content_base64: await toBase64(file), permission_scope: ["public"],
        }),
      });
      await refreshKnowledge();
      showMessage("本地文件已进入受管知识导入管道。");
    } catch (error) { showMessage(error.message, true); }
  });

  $("#knowledge-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = await api("/api/v1/console/knowledge/search", {
        method: "POST",
        body: JSON.stringify({ request_id: requestId("k-search"), trace_id: requestId("trace"), query: $("#knowledge-query").value }),
      });
      const result = body.result || {};
      $("#knowledge-search-result").innerHTML = (result.claims || []).map((claim) => `<div class="list-item"><strong>${escapeHtml(claim.field)}：${escapeHtml(claim.value)}</strong><div class="list-meta">证据 ${escapeHtml(claim.evidence_id || "-")}</div></div>`).join("") || `<div class="empty-state">${escapeHtml((result.warnings || ["没有可引用证据"])[0])}</div>`;
    } catch (error) { showMessage(error.message, true); }
  });

  $("#refresh-knowledge").addEventListener("click", () => refreshKnowledge().catch((error) => showMessage(error.message, true)));
  $("#knowledge-documents").addEventListener("click", async (event) => {
    const button = event.target.closest(".knowledge-review");
    if (!button) return;
    try {
      const reason = button.dataset.action === "reject" ? (window.prompt("请输入拒绝原因", "控制台人工复核") || "控制台人工复核") : "控制台人工复核";
      await api(`/api/v1/console/knowledge/documents/${encodeURIComponent(button.dataset.id)}/review`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId("k-review"), trace_id: requestId("trace"), action: button.dataset.action, reason }),
      });
      await refreshKnowledge();
      showMessage("文档复核已记录，历史版本仍可审计。");
    } catch (error) { showMessage(error.message, true); }
  });

  async function refreshMemory() {
    const body = await api("/api/v1/console/memory");
    const units = body.units || [];
    $("#memory-summary").innerHTML = `<div class="metric-box"><strong>${units.length}</strong><span class="muted">记忆单元</span></div><div class="metric-box"><strong>${(body.clusters || []).length}</strong><span class="muted">记忆星团</span></div><div class="metric-box"><strong>${Object.values(body.generations || {}).reduce((sum, items) => sum + items.length, 0)}</strong><span class="muted">generation 快照</span></div><div class="metric-box"><strong>${(body.streams || []).length}</strong><span class="muted">流会话</span></div>`;
    $("#memory-list").innerHTML = units.slice(-30).reverse().map((unit) => `<article class="list-item"><strong>${escapeHtml(unit.unit_id)} ${pill(unit.status)}<span class="source-kind">${escapeHtml(unit.source_ref || "-")}</span></strong><div class="list-meta">树 ${escapeHtml(unit.tree_id)} · 日期 ${escapeHtml(unit.day_key || "未知")} · 权限 ${escapeHtml(unit.permission_scope)}</div><div class="list-meta">特征：${escapeHtml((unit.features || []).map((feature) => `${feature.name}:${feature.status}`).join("，"))}</div></article>`).join("") || `<div class="empty-state">尚无记忆单元。请手工导入事件/文件，或创建并启动真实流会话。</div>`;
    if (state.streamId) await refreshStream();
  }

  async function refreshSources() {
    const body = await api("/api/v1/console/memory/stream-sources");
    $("#camera-device").innerHTML = (body.sources || []).map((source) => `<option value="${source.device_index}">${escapeHtml(source.label)} · index ${source.device_index} · ${escapeHtml(source.status)}</option>`).join("") || `<option value="0">未枚举到设备（index 0）</option>`;
  }

  $$(".stream-tab").forEach((button) => button.addEventListener("click", () => {
    state.streamKind = button.dataset.kind;
    $$(".stream-tab").forEach((item) => item.classList.toggle("active", item === button));
    $("#camera-fields").classList.toggle("hidden", state.streamKind !== "camera");
    $("#rtsp-fields").classList.toggle("hidden", state.streamKind !== "rtsp");
  }));
  $("#refresh-sources").addEventListener("click", () => refreshSources().catch((error) => showMessage(error.message, true)));
  $("#stream-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = await api("/api/v1/console/memory/streams", {
        method: "POST",
        body: JSON.stringify({
          kind: state.streamKind, device_index: state.streamKind === "camera" ? Number($("#camera-device").value) : null,
          rtsp_url: state.streamKind === "rtsp" ? $("#rtsp-url").value : null,
          sample_interval_seconds: Number($("#sample-interval").value),
        }),
      });
      state.streamId = body.session_id;
      $("#stream-actions").classList.remove("hidden");
      $("#stream-annotation-form").classList.remove("hidden");
      renderStream(body);
      showMessage("流会话已创建；设备尚未打开，点击启动采集才会连接。");
    } catch (error) { showMessage(error.message, true); }
  });

  function renderStream(session) {
    const item = session.session || session;
    $("#stream-state").className = "stream-state list-item";
    $("#stream-state").innerHTML = `<strong>${escapeHtml(item.status)} · ${escapeHtml(item.source_summary)}</strong><div class="list-meta">采样 ${item.frames_captured || 0} 帧 · 受管片段 ${item.segments_ingested || 0} · 重连 ${item.reconnect_attempts || 0}</div><div class="list-meta">${escapeHtml(item.last_error_message || "无错误")}</div>`;
    if (item.status === "running") {
      $("#stream-preview").classList.remove("hidden");
      $("#stream-preview").src = `/api/v1/console/memory/streams/${encodeURIComponent(state.streamId)}/preview?ts=${Date.now()}`;
    }
  }

  async function refreshStream() {
    if (!state.streamId) return;
    renderStream(await api(`/api/v1/console/memory/streams/${encodeURIComponent(state.streamId)}`));
  }

  $("#start-stream").addEventListener("click", async () => {
    try {
      renderStream(await api(`/api/v1/console/memory/streams/${encodeURIComponent(state.streamId)}/start`, { method: "POST" }));
      clearInterval(state.poller);
      state.poller = setInterval(() => refreshStream().catch(() => {}), 1500);
      showMessage("已显式启动真实采集；帧会按采样策略进入记忆脑。");
    } catch (error) { showMessage(error.message, true); }
  });
  $("#stop-stream").addEventListener("click", async () => {
    try {
      renderStream(await api(`/api/v1/console/memory/streams/${encodeURIComponent(state.streamId)}/stop`, { method: "POST" }));
      clearInterval(state.poller);
      showMessage("采集已停止，服务端已清除该流会话凭据。");
    } catch (error) { showMessage(error.message, true); }
  });
  $("#stream-annotation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api(`/api/v1/console/memory/streams/${encodeURIComponent(state.streamId)}/annotations`, { method: "POST", body: JSON.stringify({ text: $("#stream-annotation").value }) });
      $("#stream-annotation").value = "";
      await refreshMemory();
      showMessage("用户事件说明已作为独立文本记忆写入。");
    } catch (error) { showMessage(error.message, true); }
  });
  $("#memory-import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const file = $("#memory-file").files[0];
      const kind = $("#memory-kind").value;
      if (kind !== "text" && !file) throw new Error("图片、视频或音频导入需要选择本地文件");
      const body = {
        request_id: requestId("m-import"), trace_id: requestId("trace"), filename: file?.name || "manual-event.txt", media_kind: kind,
        text: $("#memory-text").value, timestamp: $("#memory-time").value ? new Date($("#memory-time").value).toISOString() : null,
        place: $("#memory-place").value, permission_scope: "owner",
      };
      if (file && kind !== "text") body.content_base64 = await toBase64(file);
      await api("/api/v1/console/memory/imports", { method: "POST", body: JSON.stringify(body) });
      await refreshMemory();
      showMessage("离线输入已通过 MemoryBrain.ingest 写入；媒体语义能力按实际配置返回。");
    } catch (error) { showMessage(error.message, true); }
  });

  function renderDatasetOptions(datasets) {
    const options = (datasets || []).filter((item) => Object.keys(item.metrics || {}).length).map((item) => `<option value="${escapeHtml(item.dataset_id)}">${escapeHtml(item.label)}（${escapeHtml(item.dataset_id)}）</option>`).join("");
    ["#structured-input-dataset", "#structured-query-dataset"].forEach((selector) => {
      const select = $(selector);
      const previous = select.value;
      select.innerHTML = `<option value="">请选择数据集</option>${options}`;
      select.value = previous;
    });
  }

  async function refreshStructured() {
    const body = await api("/api/v1/console/structured");
    $("#dataset-list").innerHTML = (body.datasets || []).map((item) => `<div class="list-item"><strong>${escapeHtml(item.label || item.dataset_id)}</strong><div class="list-meta">${escapeHtml(item.dataset_id)} · 指标 ${escapeHtml(Object.keys(item.metrics || {}).join("、") || "无聚合指标")}</div></div>`).join("");
    const registrations = body.registrations || [];
    $("#structured-registrations").innerHTML = registrations.length ? registrations.map((item) => `<div class="list-item"><strong>${escapeHtml(item.dataset_id)}<span class="source-kind">${escapeHtml(item.source_kind)}</span></strong><div class="list-meta">记录 ${escapeHtml(item.record_count)} · ${escapeHtml(item.updated_at)}</div><div class="list-meta">${escapeHtml(pretty(item.aggregations))}</div></div>`).join("") : `<div class="empty-state">尚无手工写入数据。请在下方输入聚合指标。</div>`;
    renderDatasetOptions(body.datasets);
  }

  $("#structured-import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const parsed = JSON.parse($("#structured-aggregations").value);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || !Object.keys(parsed).length) throw new Error("聚合指标必须是非空 JSON 对象");
      const aggregations = Object.fromEntries(Object.entries(parsed).map(([key, value]) => {
        if (typeof value !== "string" && typeof value !== "number") throw new Error(`指标 ${key} 的值必须是数字或数字字符串`);
        return [key, String(value)];
      }));
      await api("/api/v1/console/structured/imports", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId("s-import"), trace_id: requestId("trace"), dataset_id: $("#structured-input-dataset").value,
          aggregations, record_count: Number($("#structured-record-count").value),
        }),
      });
      await refreshStructured();
      showMessage("本地测试指标已写入结构化脑；可立即在右侧查询并查看证据。");
    } catch (error) { showMessage(error.message, true); }
  });

  $("#structured-query-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = await api("/api/v1/console/structured/query", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId("s-query"), trace_id: requestId("trace"), dataset_id: $("#structured-query-dataset").value,
          raw_query: $("#structured-query-text").value,
        }),
      });
      $("#structured-query-result").classList.remove("hidden");
      $("#structured-query-result").textContent = pretty(body);
      showMessage("结构化查询完成；结果中保留状态、定点值、证据与限制。");
    } catch (error) { showMessage(error.message, true); }
  });

  async function refreshTopology() { $("#topology-json").textContent = pretty(await api("/api/v1/console/topology")); }
  async function refreshGovernance() {
    const body = await api("/api/v1/console/governance");
    const decisions = body.gate_decisions || [];
    $("#governance-list").innerHTML = decisions.length ? decisions.map((item) => {
      const canAct = ["pending_review", "approved", "rejected"].includes(item.status);
      return `<div class="list-item"><strong>${escapeHtml(item.object_id)} ${pill(item.status)}</strong><div class="list-meta">${escapeHtml(item.category || "普通")}: ${escapeHtml((item.reasons || []).join("；"))}</div>${canAct ? `<div class="item-actions"><button class="secondary governance-action" data-id="${escapeHtml(item.object_id)}" data-action="approve">批准</button><button class="secondary governance-action" data-id="${escapeHtml(item.object_id)}" data-action="reject">拒绝</button><button class="secondary governance-action" data-id="${escapeHtml(item.object_id)}" data-action="rollback">回滚</button></div>` : ""}</div>`;
    }).join("") : `<div class="empty-state">尚无门禁决定。导入高风险知识资料或执行查询后可在此观察并操作。</div>`;
  }

  $("#governance-list").addEventListener("click", async (event) => {
    const button = event.target.closest(".governance-action");
    if (!button) return;
    try {
      let reason = null;
      if (button.dataset.action === "reject") {
        reason = window.prompt("请输入拒绝原因", "控制台人工拒绝");
        if (reason === null) return;
      }
      await api(`/api/v1/console/governance/${encodeURIComponent(button.dataset.id)}`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId("gate"), trace_id: requestId("trace"), action: button.dataset.action, reason }),
      });
      await refreshGovernance();
      await refreshOverview();
      showMessage("治理动作已执行，并保留审计记录。");
    } catch (error) { showMessage(error.message, true); }
  });

  $("#refresh-memory").addEventListener("click", () => refreshMemory().catch((error) => showMessage(error.message, true)));
  $("#refresh-topology").addEventListener("click", () => refreshTopology().catch((error) => showMessage(error.message, true)));
  $("#refresh-governance").addEventListener("click", () => refreshGovernance().catch((error) => showMessage(error.message, true)));
  $("#load-fixtures").addEventListener("click", async () => {
    try {
      renderBrainCards(await api("/api/v1/console/fixtures/load", { method: "POST" }));
      showMessage("可选夹具已加载；所有夹具记录都带 demo_fixture 来源标签。");
    } catch (error) { showMessage(error.message, true); }
  });
  $("#clear-runtime").addEventListener("click", async () => {
    if (!window.confirm("这会清空当前本地服务内存中的夹具和手工输入，是否继续？")) return;
    try {
      renderBrainCards(await api("/api/v1/console/fixtures/reset", { method: "POST" }));
      $("#query-result").innerHTML = "";
      $("#knowledge-search-result").innerHTML = "";
      $("#structured-query-result").textContent = "";
      $("#structured-query-result").classList.add("hidden");
      showMessage("本地验收运行态已清空。");
    } catch (error) { showMessage(error.message, true); }
  });
  $("#refresh-overview").addEventListener("click", () => refreshOverview().catch((error) => showMessage(error.message, true)));
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#logout").addEventListener("click", async () => {
    try { await api("/api/v1/console/auth/logout", { method: "POST" }); }
    finally {
      clearInterval(state.poller);
      state.session = null;
      $("#app-view").classList.add("hidden");
      $("#login-view").classList.remove("hidden");
    }
  });
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = await api("/api/v1/console/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: $("#login-user").value, password: $("#login-password").value }),
      });
      state.session = body.session;
      await loadSession();
      $("#login-message").textContent = "";
    } catch (error) { $("#login-message").textContent = error.message; }
  });

  loadSession();
})();
