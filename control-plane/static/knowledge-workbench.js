(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { jobId: null, runtime: null };

  function requestId(prefix) {
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  }

  function headers() {
    return {
      "Content-Type": "application/json",
      "X-Principal": $("#principal").value.trim(),
      "X-Roles": $("#roles").value.trim()
    };
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...headers(), ...(options.headers || {}) }
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || `请求失败 (${response.status})`);
    return body;
  }

  function statusClass(status) {
    return String(status || "").replaceAll("-", "_");
  }

  function pill(status) {
    return `<span class="status-pill ${statusClass(status)}">${escapeHtml(status || "unknown")}</span>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function setMessage(text, isError = false) {
    const target = $("#import-message");
    target.textContent = text;
    target.classList.toggle("error", isError);
  }

  function renderRuntime(runtime) {
    state.runtime = runtime;
    const meta = runtime.runtime || {};
    $("#runtime-name").textContent = runtime.runtime_profile || "-";
    $("#runtime-lock").textContent = meta.upstream_status || "-";
    const pillNode = $("#runtime-pill");
    pillNode.textContent = meta.health_status === "ready" ? "运行时可用" : "运行时未就绪";
    pillNode.className = `status-pill ${meta.health_status === "ready" ? "active" : "pending_review"}`;
  }

  function renderJob(job) {
    state.jobId = job.job_id;
    $("#task-empty").classList.add("hidden");
    $("#task-detail").classList.remove("hidden");
    $("#task-title").textContent = job.title || job.job_id;
    const status = $("#task-status");
    status.textContent = job.status;
    status.className = `status-pill ${statusClass(job.status)}`;
    $("#task-steps").innerHTML = job.steps.map((step) => `
      <div class="step ${statusClass(step.stage)}"><span class="step-dot"></span><span>${escapeHtml(step.stage)}${step.message ? `：${escapeHtml(step.message)}` : ""}</span><time>${new Date(step.at).toLocaleTimeString()}</time></div>
    `).join("");
    const error = $("#task-error");
    if (job.error_code) {
      error.textContent = `${job.error_code}：${job.error_message || ""}`;
      error.classList.remove("hidden");
    } else error.classList.add("hidden");
    $("#retry-task").classList.toggle("hidden", job.status !== "failed");
    $("#cancel-task").classList.toggle("hidden", ["published", "cancelled"].includes(job.status));
  }

  function renderDocuments(documents) {
    $("#document-count").textContent = documents.length;
    const target = $("#document-list");
    if (!documents.length) { target.className = "list empty-state"; target.textContent = "尚未导入资料。"; return; }
    target.className = "list";
    target.innerHTML = documents.map((item) => {
      const document = item.document;
      const publication = item.publication || {};
      return `<article class="list-item"><strong>${escapeHtml(document.title || document.document_id)}</strong><div class="list-meta">${escapeHtml(document.document_id)} · 单元 ${item.unit_count} · ${pill(publication.status || document.document_status)}</div><div class="item-actions"><button class="secondary document-view" data-document-id="${escapeHtml(document.document_id)}" type="button">展开证据</button>${publication.status === "pending_review" ? `<button class="secondary document-review" data-document-id="${escapeHtml(document.document_id)}" data-review-action="approve" type="button">批准发布</button><button class="secondary document-review" data-document-id="${escapeHtml(document.document_id)}" data-review-action="reject" type="button">拒绝</button>` : ""}</div></article>`;
    }).join("");
  }

  function renderClusters(clusters) {
    $("#cluster-count").textContent = clusters.length;
    const target = $("#cluster-list");
    if (!clusters.length) { target.className = "list empty-state"; target.textContent = "尚无自动生成的知识星团。"; return; }
    target.className = "list";
    target.innerHTML = clusters.map((cluster) => `<article class="list-item"><strong>${escapeHtml(cluster.cluster_id)}</strong><div class="list-meta">${escapeHtml(cluster.tree_id)} · 成员 ${cluster.member_unit_ids.length} · 独立来源 ${cluster.independent_source_count} · generation ${cluster.generation ?? "-"}</div><div class="list-meta">${pill(cluster.status)} 索引 ${escapeHtml(cluster.index_version || "待生成")}</div><div class="item-actions"><select class="cluster-action" data-cluster-id="${escapeHtml(cluster.cluster_id)}"><option value="pause">暂停星团</option><option value="rebuild">重建投影</option><option value="split">请求拆分</option><option value="merge">请求合并</option></select><button class="secondary apply-correction" data-cluster-id="${escapeHtml(cluster.cluster_id)}" type="button">提交纠偏</button></div></article>`).join("");
  }

  function renderLinks(links) {
    const target = $("#link-list");
    if (!links.length) { target.className = "list empty-state"; target.textContent = "知识星团发布后会基于公共锚点生成关联候选。"; return; }
    target.className = "list";
    target.innerHTML = links.map((link) => `<article class="list-item"><strong>${escapeHtml(link.anchor_value)}</strong><div class="list-meta">知识脑 → ${escapeHtml(link.target_brain)} · ${pill(link.status)}</div><div class="list-meta">${escapeHtml(link.basis)}</div></article>`).join("");
  }

  function renderSearch(response) {
    const result = response.result || {};
    const route = response.route || {};
    const target = $("#search-result");
    const selected = route.selected || [];
    const claims = result.claims || [];
    if (!selected.length && !claims.length) { target.className = "empty-state"; target.textContent = (result.warnings || ["没有可引用证据"])[0]; return; }
    target.className = "";
    target.innerHTML = `
      <article class="route-card"><strong>检索轨迹</strong><div class="route-score">${escapeHtml(route.retrieval_version || "-")} · 候选 ${route.candidates?.length || 0} · 选中 ${selected.length} · 星团 ${escapeHtml(route.cluster_id || "未命中")}</div></article>
      ${claims.map((claim) => `<article class="route-card"><strong>${escapeHtml(claim.field)}</strong><div>${escapeHtml(claim.value)}</div><div class="route-score">证据 ${escapeHtml(claim.evidence_id || "-")} · 单元 ${escapeHtml(claim.unit || "-")}</div></article>`).join("")}
    `;
  }

  async function refresh() {
    try {
      const [runtime, docs, clusters, links] = await Promise.all([
        api("/api/v1/knowledge/runtime"),
        api("/api/v1/knowledge/documents"),
        api("/api/v1/knowledge/clusters"),
        api("/api/v1/topology/cross-brain-links")
      ]);
      renderRuntime(runtime);
      renderDocuments(docs.documents || []);
      renderClusters(clusters.clusters || []);
      renderLinks(links.links || []);
      if (state.jobId) {
        const job = await api(`/api/v1/knowledge/imports/${encodeURIComponent(state.jobId)}`);
        renderJob(job.job);
      }
    } catch (error) { setMessage(error.message, true); }
  }

  async function startJob(job) {
    renderJob(job);
    const started = await api(`/api/v1/knowledge/imports/${encodeURIComponent(job.job_id)}/start`, { method: "POST" });
    renderJob(started.job);
    await refresh();
  }

  document.querySelectorAll(".source-tab").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".source-tab").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".source-form").forEach((form) => form.classList.add("hidden"));
    $(`#${button.dataset.source}-form`).classList.remove("hidden");
    setMessage("");
  }));

  $("#text-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/v1/knowledge/imports/text", { method: "POST", body: JSON.stringify({ request_id: requestId("text"), trace_id: requestId("trace"), title: $("#text-title").value || null, content: $("#text-content").value, permission_scope: $("#text-permission").value.split(",").map((item) => item.trim()).filter(Boolean) }) });
      await startJob(response.job);
      setMessage("导入已完成，已刷新知识树投影。");
    } catch (error) { setMessage(error.message, true); }
  });

  $("#file-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("#file-input").files[0];
    if (!file) { setMessage("请选择文件。", true); return; }
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = ""; bytes.forEach((item) => { binary += String.fromCharCode(item); });
      const response = await api("/api/v1/knowledge/imports/files", { method: "POST", body: JSON.stringify({ request_id: requestId("file"), trace_id: requestId("trace"), filename: file.name, media_kind: $("#file-kind").value, content_base64: btoa(binary), permission_scope: $("#file-permission").value.split(",").map((item) => item.trim()).filter(Boolean) }) });
      await startJob(response.job);
      setMessage("文件任务已执行；未配置的解析器会明确显示不可用原因。");
    } catch (error) { setMessage(error.message, true); }
  });

  $("#url-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/v1/knowledge/imports/urls", { method: "POST", body: JSON.stringify({ request_id: requestId("url"), trace_id: requestId("trace"), url: $("#url-value").value, approved: $("#url-approved").checked }) });
      renderJob(response.job);
      setMessage("已创建 URL 草稿。获批且配置抓取器后才可启动网络访问。");
    } catch (error) { setMessage(error.message, true); }
  });

  $("#search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/v1/knowledge/search", { method: "POST", body: JSON.stringify({ request_id: requestId("search"), trace_id: requestId("trace"), query: $("#search-query").value }) });
      renderSearch(response);
    } catch (error) { $("#search-result").className = "empty-state"; $("#search-result").textContent = error.message; }
  });

  $("#retry-task").addEventListener("click", async () => { if (!state.jobId) return; try { const response = await api(`/api/v1/knowledge/imports/${encodeURIComponent(state.jobId)}/retry`, { method: "POST" }); renderJob(response.job); await refresh(); } catch (error) { setMessage(error.message, true); } });
  $("#cancel-task").addEventListener("click", async () => { if (!state.jobId) return; try { const response = await api(`/api/v1/knowledge/imports/${encodeURIComponent(state.jobId)}/cancel`, { method: "POST" }); renderJob(response.job); } catch (error) { setMessage(error.message, true); } });
  document.addEventListener("click", async (event) => {
    const view = event.target.closest(".document-view");
    if (view) {
      try {
        const detail = await api(`/api/v1/knowledge/documents/${encodeURIComponent(view.dataset.documentId)}`);
        const target = $("#document-detail");
        target.className = "detail-box";
        target.innerHTML = `<h3>${escapeHtml(detail.document.title || detail.document.document_id)} · 证据定位</h3>${detail.units.map((unit) => `<div class="detail-row">${escapeHtml(unit.unit_id)} · 层级 ${unit.chunk_level ?? "-"} · 父块 ${escapeHtml(unit.parent_unit_id || "-")} · ${escapeHtml(unit.locator?.anchor || "无锚点")}</div>`).join("")}${detail.evidence.map((evidence) => `<div class="detail-row">证据 ${escapeHtml(evidence.evidence_id)} · ${escapeHtml(evidence.source_object_ref || "无受管对象引用")}</div>`).join("")}`;
      } catch (error) { setMessage(error.message, true); }
      return;
    }
    const review = event.target.closest(".document-review");
    if (review) {
      try {
        await api(`/api/v1/knowledge/documents/${encodeURIComponent(review.dataset.documentId)}/review`, { method: "POST", body: JSON.stringify({ request_id: requestId("review"), trace_id: requestId("trace"), action: review.dataset.reviewAction, reason: "工作台例外复核" }) });
        await refresh();
      } catch (error) { setMessage(error.message, true); }
      return;
    }
    const correction = event.target.closest(".apply-correction");
    if (correction) {
      const clusterId = correction.dataset.clusterId;
      const action = document.querySelector(`.cluster-action[data-cluster-id="${CSS.escape(clusterId)}"]`).value;
      try {
        const response = await api(`/api/v1/knowledge/clusters/${encodeURIComponent(clusterId)}/corrections`, { method: "POST", body: JSON.stringify({ request_id: requestId("correction"), trace_id: requestId("trace"), action, reason: `工作台纠偏：${action}` }) });
        setMessage(`已记录 ${action} 纠偏请求，星团状态：${response.cluster.status}`);
        await refresh();
      } catch (error) { setMessage(error.message, true); }
    }
  });
  $("#refresh").addEventListener("click", refresh);
  refresh();
})();
