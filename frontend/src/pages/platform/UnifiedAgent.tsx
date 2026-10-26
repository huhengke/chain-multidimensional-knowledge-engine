import {
  ApartmentOutlined, BranchesOutlined, CopyOutlined, DeleteOutlined, DislikeFilled,
  DislikeOutlined, EditOutlined, InboxOutlined, LikeFilled, LikeOutlined, MoreOutlined,
  FullscreenOutlined, PauseCircleOutlined, PlusOutlined, PushpinFilled, PushpinOutlined, RobotOutlined, SendOutlined,
  SettingOutlined, ToolOutlined, UndoOutlined, UserOutlined,
} from "@ant-design/icons";
import {
  Alert, Button, Checkbox, Collapse, Drawer, Dropdown, Empty, Input, List, Modal,
  Segmented, Select, Space, Spin, Tag, Tooltip, Typography, message,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { errorMessage, http } from "../../api/client";
import { listAgentModels } from "../../api/endpoints";
import type { AgentMessage, AgentSession } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import AgentMessageContent from "../../components/AgentMessageContent";
import SkillCenter from "../../components/SkillCenter";
import UnifiedRunPanel from "./UnifiedRunPanel";
import { API, Heading, LoadError, brainName } from "./shared";
import "../Agent.css";

type SessionView = "active" | "archived";
type Feedback = "up" | "down" | null;
type Conversation = Record<string, any>;
type ConversationMessage = Record<string, any>;
type Brain = "structured" | "knowledge" | "memory";
type ModelDeployment = { id: string; name: string; default: boolean; brains: string[] };

const brains: Brain[] = ["structured", "knowledge", "memory"];

export default function UnifiedAgent() {
  const { user } = useAuth();
  const [messageApi, messageContext] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [sessionView, setSessionView] = useState<SessionView>("active");
  const [active, setActive] = useState<Conversation | null>(null);
  const [selectedRun, setSelectedRun] = useState<any>(null);
  const [input, setInput] = useState("");
  const [routeMode, setRouteMode] = useState<"auto" | "scoped">("auto");
  const [answerMode, setAnswerMode] = useState<"standard" | "fast">("standard");
  const [requestedBrains, setRequestedBrains] = useState<Brain[]>(["structured", "knowledge", "memory"]);
  const [deployments, setDeployments] = useState<ModelDeployment[]>([]);
  const [deployment, setDeployment] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [error, setError] = useState("");
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [branchingMessageId, setBranchingMessageId] = useState<string | null>(null);
  const [specialistsOpen, setSpecialistsOpen] = useState(false);
  const [skillCenterOpen, setSkillCenterOpen] = useState(false);
  const [skillModelId, setSkillModelId] = useState("");
  const activeIdRef = useRef<string | undefined>();
  const running = busy || active?.run?.status === "running";
  const canScope = user?.role === "admin" || user?.role === "operator";
  const canSend = Boolean(input.trim()) && !running && !active?.archived
    && !(routeMode === "scoped" && !requestedBrains.length);

  const loadSessions = useCallback(async (view: SessionView, preferredId?: string) => {
    setLoadingSessions(true);
    try {
      const response = await http.get(API + "/sessions", { params: { archived: view === "archived" } });
      const items = (response.data?.items || []).filter((item: any) => item.brain === "unified");
      setSessions(items);
      const nextId = preferredId && items.some((item: any) => item.id === preferredId) ? preferredId : items[0]?.id;
      activeIdRef.current = nextId;
      setActive(nextId ? (await http.get(API + `/sessions/${nextId}`)).data : null);
      setSelectedRun(null);
      setError("");
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setLoadingSessions(false); }
  }, []);

  const reloadListOnly = async () => {
    const response = await http.get(API + "/sessions", { params: { archived: sessionView === "archived" } });
    setSessions((response.data?.items || []).filter((item: any) => item.brain === "unified"));
  };

  useEffect(() => { void loadSessions("active"); }, [loadSessions]);
  useEffect(() => {
    void listAgentModels()
      .then(result => setSkillModelId(result.active || result.items[0]?.id || ""))
      .catch(() => { /* Skill management remains available; AI drafting needs a configured model. */ });
  }, []);
  useEffect(() => {
    void http.get(API + "/model-deployments")
      .then(response => {
        const items: ModelDeployment[] = response.data?.items || [];
        setDeployments(items);
        setDeployment(current => current || response.data?.default || items[0]?.id || "");
      })
      .catch(() => { /* The server still applies its own default deployment. */ });
  }, []);
  useEffect(() => {
    if (active?.model_deployment && deployments.some(item => item.id === active.model_deployment)) {
      setDeployment(active.model_deployment);
    }
  }, [active?.id, active?.model_deployment, deployments]);
  useEffect(() => { activeIdRef.current = active?.id; }, [active?.id]);
  useEffect(() => {
    if (!active?.id || active?.run?.status !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const sessionId = active.id;
    const runId = active.run.id;
    const poll = async () => {
      try {
        const run = (await http.get(API + `/runs/${runId}`)).data;
        if (cancelled || activeIdRef.current !== sessionId) return;
        if (run.status === "running") {
          setActive(current => current?.id === sessionId ? { ...current, run } : current);
          timer = setTimeout(poll, 700);
          return;
        }
        setActive((await http.get(API + `/sessions/${sessionId}`)).data);
        void reloadListOnly();
      } catch { /* The existing visible state remains authoritative until the next poll. */ }
    };
    timer = setTimeout(poll, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active?.id, active?.run?.id, active?.run?.status]);

  const createSession = async () => {
    try {
      const created = (await http.post(API + "/sessions", { brain: "unified" })).data;
      setSessionView("active");
      await loadSessions("active", created.id);
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const chooseSession = async (id: string) => {
    try { activeIdRef.current = id; setActive((await http.get(API + `/sessions/${id}`)).data); setSelectedRun(null); setError(""); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const refreshCurrentList = async (preferredId?: string) => loadSessions(sessionView, preferredId || active?.id);
  const togglePin = async (item: Conversation) => { try { await http.post(API + `/sessions/${item.id}/pin`, { pinned: !item.pinned }); await refreshCurrentList(item.id); } catch (cause) { setError(errorMessage(cause)); } };
  const toggleArchive = async (item: Conversation) => {
    try { await http.post(API + `/sessions/${item.id}/archive`, { archived: !item.archived }); messageApi.success(item.archived ? "会话已恢复" : "会话已归档"); await loadSessions(sessionView); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const startRename = (item: Conversation) => { setRenameTarget(item); setRenameValue(item.title); };
  const renameSession = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    try { await http.post(API + `/sessions/${renameTarget.id}/rename`, { title: renameValue.trim() }); setRenameTarget(null); await refreshCurrentList(renameTarget.id); messageApi.success("会话已重命名"); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const confirmDelete = (item: Conversation) => modalApi.confirm({
    title: "删除会话？", content: `“${item.title}”及其运行记录将被删除，此操作不能恢复。`, okText: "删除",
    okButtonProps: { danger: true }, cancelText: "取消", onOk: async () => {
      try { await http.delete(API + `/sessions/${item.id}`); await loadSessions(sessionView); messageApi.success("会话已删除"); }
      catch (cause) { setError(errorMessage(cause)); }
    },
  });
  const setFeedback = async (item: ConversationMessage, value: Exclude<Feedback, null>) => {
    if (!active) return;
    const feedback = item.feedback === value ? null : value;
    try {
      const updated = (await http.post(API + `/sessions/${active.id}/messages/${item.id}/feedback`, { feedback })).data;
      setActive({ ...active, messages: active.messages.map((entry: any) => entry.id === updated.id ? updated : entry) });
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const branchFrom = async (item: ConversationMessage) => {
    if (!active || branchingMessageId) return;
    setBranchingMessageId(item.id);
    try { const branch = (await http.post(API + `/sessions/${active.id}/messages/${item.id}/branch`)).data; setSessionView("active"); await loadSessions("active", branch.id); messageApi.success("已创建统一分枝会话"); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setBranchingMessageId(null); }
  };
  const chooseRun = async (id: string) => {
    if (id === active?.last_run_id) { setSelectedRun(null); return; }
    try { setSelectedRun((await http.get(API + `/runs/${id}`)).data); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const send = async () => {
    if (!canSend) return;
    setBusy(true); setError(""); setSelectedRun(null);
    let sessionId: string | undefined;
    try {
      const conversation = active || (await http.post(API + "/sessions", { brain: "unified" })).data;
      sessionId = conversation.id; activeIdRef.current = conversation.id; setActive(conversation);
      const response = await http.post(API + `/unified/sessions/${conversation.id}/runs`, {
        query: input, route_mode: routeMode, requested_brains: routeMode === "scoped" ? requestedBrains : [],
        model_deployment: deployment, answer_mode: answerMode,
      });
      if (activeIdRef.current === conversation.id) setActive(response.data);
      setInput("");
      await reloadListOnly();
    } catch (cause) { setError(errorMessage(cause)); }
    finally {
      if (sessionId && activeIdRef.current === sessionId) { try { setActive((await http.get(API + `/sessions/${sessionId}`)).data); } catch { /* preserve error */ } }
      setBusy(false);
    }
  };
  const pauseRun = async () => {
    const runId = active?.run?.id;
    if (!active?.id || !runId) return;
    try {
      await http.post(API + `/sessions/${active.id}/runs/${runId}/pause`);
      setActive((await http.get(API + `/sessions/${active.id}`)).data);
      messageApi.info("已暂停 3+1 执行；已完成的星团与脑核验结果已保留");
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const retractRun = async () => {
    const runId = active?.run?.id;
    if (!active?.id || !runId) return;
    try {
      const response = await http.delete(API + `/sessions/${active.id}/runs/${runId}/question`);
      setActive(response.data);
      await reloadListOnly();
      messageApi.success("已撤回本次问题");
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const copyAnswer = async (item: ConversationMessage) => { try { await navigator.clipboard.writeText(item.content || ""); messageApi.success("已复制回答"); } catch { setError("无法访问剪贴板"); } };
  const runOptions = (active?.messages || []).filter((item: any) => item.role === "user" && item.run_id).map((item: any) => ({ value: item.run_id, label: item.content.slice(0, 25) }));
  const liveAnswer = active?.run?.status === "running" ? String(active.run.live_answer || "") : "";
  const skillSession = active ? {
    id: active.id,
    harness_session_id: null,
    model_id: skillModelId || null,
    model_name: null,
    pinned: Boolean(active.pinned),
    archived: Boolean(active.archived),
    skill_mode: active.skill_mode || "auto",
    selected_skills: active.selected_skills || [],
    title: active.title || "统一会话",
    created_at: active.created_at,
    updated_at: active.updated_at,
    message_count: active.messages?.length || 0,
    last_run_id: active.last_run_id || null,
    messages: active.messages || [],
  } as AgentSession : null;
  const updateSkillSession = (next: AgentSession) => {
    setActive(current => current ? { ...current, skill_mode: next.skill_mode, selected_skills: next.selected_skills } : current);
  };

  return <>
    {messageContext}{modalContext}
    <Heading title="统一 Agent 工作台" description="单一入口执行记忆星团快速线索、三脑独立核验与证据合一；各脑模型、智慧树和证据边界保持独立。" extra={<Button icon={<SettingOutlined />} onClick={() => setSpecialistsOpen(true)}>专脑管理与诊断</Button>} />
    <LoadError error={error} />
    <div className="cognitive-agent-workspace cognitive-agent-enhanced unified-agent-workspace">
      <aside className="conversation-list">
        <div className="cognitive-session-heading"><strong>会话</strong><Tooltip title="新建统一会话"><Button type="text" icon={<PlusOutlined />} disabled={running} onClick={() => void createSession()} aria-label="新建会话" /></Tooltip></div>
        <Segmented block size="small" value={sessionView} onChange={value => { setSessionView(value as SessionView); void loadSessions(value as SessionView); }} options={[{ label: "会话", value: "active" }, { label: "归档", value: "archived", icon: <InboxOutlined /> }]} />
        {loadingSessions ? <Spin className="cognitive-list-spinner" /> : <List dataSource={sessions} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessionView === "archived" ? "暂无归档会话" : "暂无统一会话"} /> }} renderItem={item => <List.Item className={`cognitive-session-item ${active?.id === item.id ? "active" : ""}`} onClick={() => void chooseSession(item.id)}>
          <div className="cognitive-session-copy"><div>{item.pinned && <PushpinFilled />}<Typography.Text ellipsis>{item.title}</Typography.Text></div><small>{item.messages?.length || 0} 条消息</small></div>
          <Dropdown trigger={["click"]} menu={{ items: [
            ...(!item.archived ? [{ key: "pin", icon: item.pinned ? <PushpinOutlined /> : <PushpinFilled />, label: item.pinned ? "取消置顶" : "置顶" }] : []),
            { key: "rename", icon: <EditOutlined />, label: "重命名", disabled: running && active?.id === item.id },
            { key: "archive", icon: item.archived ? <UndoOutlined /> : <InboxOutlined />, label: item.archived ? "恢复" : "归档", disabled: running && active?.id === item.id },
            { type: "divider" as const }, { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, disabled: running && active?.id === item.id },
          ], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); if (key === "pin") void togglePin(item); if (key === "rename") startRename(item); if (key === "archive") void toggleArchive(item); if (key === "delete") confirmDelete(item); } }}><Button type="text" size="small" className="cognitive-session-menu" icon={<MoreOutlined />} aria-label="会话操作" onClick={event => event.stopPropagation()} /></Dropdown>
        </List.Item>} />}
      </aside>
      <section className="conversation-main">
        <div className="conversation-toolbar">
          <Space><strong>{active?.title || "新会话"}</strong>{active?.archived && <Tag>已归档</Tag>}</Space>
          <Space size={6} wrap>
            <Tooltip title="生成、管理并为当前统一会话加载 Skill"><Button icon={<ToolOutlined />} onClick={() => setSkillCenterOpen(true)}>Skills</Button></Tooltip>
            <Tooltip title="查看三脑独立结构和本次查询路径"><Link to={`/agent/topology${active?.id ? `?session=${encodeURIComponent(active.id)}` : ""}`}><Button icon={<FullscreenOutlined />}>智慧树</Button></Link></Tooltip>
            <Tag color="blue"><ApartmentOutlined /> 3+1 自动编排</Tag>
          </Space>
        </div>
        <div className="conversation-messages cognitive-bubble-list">
          {!active?.messages?.length && <Empty style={{ marginTop: 60 }} description="输入任务，系统会先检索星团线索，再路由到一个或多个认知脑核验" />}
          {(active?.messages || []).map((item: ConversationMessage) => <div key={item.id} className={`cognitive-chat-row ${item.role}`}>
            <div className="cognitive-chat-avatar">{item.role === "user" ? <UserOutlined /> : <RobotOutlined />}</div>
            <article className="cognitive-chat-bubble">
              <strong>{item.role === "user" ? "你" : item.status === "failed" ? "执行未完成" : "统一认知助手"}{item.role === "assistant" && item.answer_mode === "fast" && <Tag color="green" style={{ marginLeft: 8 }}>快速</Tag>}</strong>
              {item.role === "assistant"
                ? <AgentMessageContent message={item as AgentMessage} />
                : <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>}
              {item.role === "assistant" && <div className="cognitive-message-actions"><Space size={2}>
                <Tooltip title="复制回答"><Button type="text" size="small" icon={<CopyOutlined />} onClick={() => void copyAnswer(item)} aria-label="复制回答" /></Tooltip>
                <Tooltip title="点赞"><Button type="text" size="small" icon={item.feedback === "up" ? <LikeFilled /> : <LikeOutlined />} aria-pressed={item.feedback === "up"} onClick={() => void setFeedback(item, "up")} /></Tooltip>
                <Tooltip title="点踩"><Button type="text" size="small" icon={item.feedback === "down" ? <DislikeFilled /> : <DislikeOutlined />} aria-pressed={item.feedback === "down"} onClick={() => void setFeedback(item, "down")} /></Tooltip>
                <Tooltip title="在新对话中分枝"><Button type="text" size="small" icon={<BranchesOutlined />} loading={branchingMessageId === item.id} disabled={running || active?.archived} onClick={() => void branchFrom(item)} /></Tooltip>
              </Space>{item.run_id && <Button size="small" type="link" onClick={() => void chooseRun(item.run_id)}>查看运行步骤</Button>}</div>}
              {item.evidence_manifest && <Collapse items={[{ key: "manifest", label: "查看三脑证据清单", children: <div className="unified-evidence-manifest">{Object.entries(item.evidence_manifest).map(([brain, value]: any) => <div key={brain}><strong>{brainName[brain]}</strong><span>{value.count} 条证据</span><small>{value.trace_id || "无追溯编号"}</small></div>)}</div> }]} />}
            </article>
          </div>)}
          {liveAnswer.trim() && <div className="cognitive-chat-row assistant">
            <div className="cognitive-chat-avatar"><RobotOutlined /></div>
            <article className="cognitive-chat-bubble">
              <strong>统一认知助手 <Tag color="processing">正在生成</Tag></strong>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{liveAnswer}</ReactMarkdown>
            </article>
          </div>}
          {running && <Alert type="info" showIcon message="3+1 编排执行中，右侧会持续更新星团、路由、核验和合一状态" action={<Button size="small" icon={<PauseCircleOutlined />} onClick={() => void pauseRun()}>暂停</Button>} />}
        </div>
        <div className="conversation-composer">
          {active?.archived && <Alert type="info" showIcon message="归档会话只读；恢复后才能继续提问" />}
          <div className="unified-route-controls">{canScope ? <Segmented value={routeMode} onChange={value => setRouteMode(value as "auto" | "scoped")} options={[{ label: "自动路由", value: "auto" }, { label: "指定脑", value: "scoped" }]} /> : <Tag color="blue">自动路由</Tag>}{canScope && routeMode === "scoped" && <Checkbox.Group value={requestedBrains} onChange={value => setRequestedBrains(value as Brain[])} options={brains.map(brain => ({ label: brainName[brain].replace("脑", ""), value: brain }))} />}
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              <span className="status-note">查询档位</span>
              <Tooltip title="两种档位走同一套检索；快速回答约 30 秒，深度分析约 2 分钟并给出双栏完整报告">
                <Segmented size="small" value={answerMode} onChange={value => setAnswerMode(value as "standard" | "fast")} options={[{ label: "快速回答", value: "fast" }, { label: "深度分析", value: "standard" }]} />
              </Tooltip>
            </div>
          </div>
          <Input.TextArea rows={3} value={input} disabled={running || active?.archived} onChange={event => setInput(event.target.value)} placeholder="输入任务或问题（Enter 发送，Shift+Enter 换行）" aria-label="统一 Agent 问题" onKeyDown={event => {
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
            event.preventDefault();
            if (canSend) void send();
          }} />
          <div className="conversation-send"><span className="status-note">星团只做快速线索；正式结论由命中的目标脑独立核验</span><Space>{deployments.length > 0 && <Tooltip title="选择本次三脑核验与结果合一使用的模型部署；默认 Codex"><Select size="small" aria-label="模型部署" style={{ width: 150 }} value={deployment || undefined} disabled={running || active?.archived} onChange={setDeployment} options={deployments.map(item => ({ value: item.id, label: item.default ? `${item.name}（默认）` : item.name }))} /></Tooltip>}{active?.run?.status === "paused" && <Button danger onClick={() => void retractRun()}>撤回问题</Button>}<Button type="primary" icon={<SendOutlined />} loading={running} disabled={!canSend} onClick={() => void send()}>发送</Button></Space></div>
        </div>
      </section>
      <UnifiedRunPanel run={selectedRun || active?.run} runOptions={runOptions} onSelect={chooseRun} />
    </div>

    <SkillCenter
      open={skillCenterOpen}
      session={skillSession}
      modelId={skillModelId}
      running={Boolean(running)}
      onClose={() => setSkillCenterOpen(false)}
      onSessionChange={updateSkillSession}
    />

    <Drawer title="专脑管理与诊断" width={440} open={specialistsOpen} onClose={() => setSpecialistsOpen(false)}>
      <Alert type="info" showIcon message="这些入口用于单脑调试、证据核对和边界诊断，不是日常统一入口。" />
      <div className="specialist-entry-list">
        <section><strong>结构化数据推理脑</strong><p>数据集、精确聚合、基座特征与结构化智慧树。</p><Link to="/structured/agent"><Button>进入专脑工作台</Button></Link></section>
        <section><strong>知识数据推理脑</strong><p>WeKnora 检索、知识智慧树、证据链和自动生长。</p><Space><Link to="/knowledge/agent"><Button>进入专脑工作台</Button></Link><Link to="/cognition?brain=knowledge"><Button>查看知识拓扑</Button></Link></Space></section>
        <section><strong>时空记忆推理脑</strong><p>媒体、实时流、时间地点检索和记忆星团。</p><Link to="/memory/agent"><Button>进入专脑工作台</Button></Link></section>
      </div>
    </Drawer>
    <Modal title="重命名统一会话" open={!!renameTarget} okText="保存" cancelText="取消" onOk={() => void renameSession()} onCancel={() => setRenameTarget(null)} okButtonProps={{ disabled: !renameValue.trim() }}><Input value={renameValue} maxLength={200} onChange={event => setRenameValue(event.target.value)} onPressEnter={() => void renameSession()} autoFocus /></Modal>
  </>;
}
