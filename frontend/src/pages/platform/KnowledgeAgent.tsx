import {
  BranchesOutlined, CopyOutlined, DeleteOutlined, DislikeFilled, DislikeOutlined,
  EditOutlined, InboxOutlined, LikeFilled, LikeOutlined, MoreOutlined, PlusOutlined,
  PauseCircleOutlined, PushpinFilled, PushpinOutlined, RobotOutlined, SendOutlined, UndoOutlined, UserOutlined,
} from "@ant-design/icons";
import {
  Alert, Button, Collapse, Dropdown, Empty, Input, List, Modal, Segmented, Select,
  Space, Spin, Tag, Tooltip, Typography, message,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { errorMessage, http } from "../../api/client";
import AgentRunPanel from "./AgentRunPanel";
import { API, Evidence, Heading, LoadError, useData } from "./shared";

type SessionView = "active" | "archived";
type Feedback = "up" | "down" | null;
type Conversation = Record<string, any>;
type ConversationMessage = Record<string, any>;

export default function KnowledgeAgent() {
  const models = useData("/models");
  const [messageApi, messageContext] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [sessionView, setSessionView] = useState<SessionView>("active");
  const [active, setActive] = useState<Conversation | null>(null);
  const [selectedRun, setSelectedRun] = useState<any>(null);
  const [model, setModel] = useState<string>();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [error, setError] = useState("");
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [branchingMessageId, setBranchingMessageId] = useState<string | null>(null);
  const activeIdRef = useRef<string | undefined>();
  const options = (models.data?.items || []).filter((item: any) => item.brain === "knowledge" && item.purpose === "chat" && item.enabled);
  const running = busy || active?.run?.status === "running";

  const loadSessions = useCallback(async (view: SessionView, preferredId?: string) => {
    setLoadingSessions(true);
    try {
      const response = await http.get(API + "/sessions", { params: { archived: view === "archived" } });
      const items = (response.data?.items || []).filter((item: any) => item.brain === "knowledge");
      setSessions(items);
      const nextId = preferredId && items.some((item: any) => item.id === preferredId) ? preferredId : items[0]?.id;
      activeIdRef.current = nextId;
      setActive(nextId ? (await http.get(API + `/sessions/${nextId}`)).data : null);
      setSelectedRun(null);
      setError("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const reloadListOnly = async (view: SessionView) => {
    const response = await http.get(API + "/sessions", { params: { archived: view === "archived" } });
    setSessions((response.data?.items || []).filter((item: any) => item.brain === "knowledge"));
  };

  useEffect(() => { void loadSessions("active"); }, [loadSessions]);
  useEffect(() => { activeIdRef.current = active?.id; }, [active?.id]);
  useEffect(() => { if (options.length && !model) setModel(options[0].id); }, [options, model]);
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
          timer = setTimeout(poll, 1200);
          return;
        }
        setActive((await http.get(API + `/sessions/${sessionId}`)).data);
        void reloadListOnly("active");
      } catch (cause) {
        if (!cancelled) setError(errorMessage(cause));
      }
    };
    timer = setTimeout(poll, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active?.id, active?.run?.id, active?.run?.status]);

  const refreshCurrentList = async (preferredId?: string) => loadSessions(sessionView, preferredId || active?.id);
  const createSession = async () => {
    try {
      const created = (await http.post(API + "/sessions", { brain: "knowledge" })).data;
      setSessionView("active");
      await loadSessions("active", created.id);
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const chooseSession = async (id: string) => {
    try { activeIdRef.current = id; setActive((await http.get(API + `/sessions/${id}`)).data); setSelectedRun(null); setError(""); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const changeView = async (value: SessionView) => { setSessionView(value); await loadSessions(value); };
  const togglePin = async (item: Conversation) => {
    try { await http.post(API + `/sessions/${item.id}/pin`, { pinned: !item.pinned }); await refreshCurrentList(item.id); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const toggleArchive = async (item: Conversation) => {
    try {
      await http.post(API + `/sessions/${item.id}/archive`, { archived: !item.archived });
      messageApi.success(item.archived ? "会话已恢复" : "会话已归档");
      await loadSessions(sessionView);
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const startRename = (item: Conversation) => { setRenameTarget(item); setRenameValue(item.title); };
  const renameSession = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    try {
      await http.post(API + `/sessions/${renameTarget.id}/rename`, { title: renameValue.trim() });
      setRenameTarget(null);
      await refreshCurrentList(renameTarget.id);
      messageApi.success("会话已重命名");
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const confirmDelete = (item: Conversation) => {
    modalApi.confirm({
      title: "删除会话？", content: `“${item.title}”及其运行记录将被删除，此操作不能恢复。`,
      okText: "删除", okButtonProps: { danger: true }, cancelText: "取消",
      onOk: async () => {
        try { await http.delete(API + `/sessions/${item.id}`); await loadSessions(sessionView); messageApi.success("会话已删除"); }
        catch (cause) { setError(errorMessage(cause)); }
      },
    });
  };
  const copyAnswer = async (item: ConversationMessage) => {
    try { await navigator.clipboard.writeText(item.content || ""); messageApi.success("已复制回答"); }
    catch { setError("无法访问剪贴板"); }
  };
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
    try {
      const branch = (await http.post(API + `/sessions/${active.id}/messages/${item.id}/branch`)).data;
      setSessionView("active");
      await loadSessions("active", branch.id);
      messageApi.success("已创建知识分枝会话");
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBranchingMessageId(null); }
  };
  const chooseRun = async (id: string) => {
    if (id === active?.last_run_id) { setSelectedRun(null); return; }
    try { setSelectedRun((await http.get(API + `/runs/${id}`)).data); }
    catch (cause) { setError(errorMessage(cause)); }
  };
  const send = async () => {
    if (!input.trim() || !model || running || active?.archived) return;
    setBusy(true); setError(""); setSelectedRun(null);
    let sessionId: string | undefined;
    try {
      const conversation = active || (await http.post(API + "/sessions", { brain: "knowledge" })).data;
      sessionId = conversation.id;
      if (!active) activeIdRef.current = conversation.id;
      setActive(conversation);
      const response = await http.post(API + `/sessions/${conversation.id}/runs`, { query: input, model_id: model });
      const stillSelected = activeIdRef.current === conversation.id;
      if (stillSelected) setActive(response.data);
      setInput("");
      if (stillSelected) await reloadListOnly("active");
      else await reloadListOnly("active");
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
      messageApi.info("已暂停本次执行；已完成的检索轨迹已保留");
    } catch (cause) { setError(errorMessage(cause)); }
  };
  const retractRun = async () => {
    const runId = active?.run?.id;
    if (!active?.id || !runId) return;
    try {
      const response = await http.delete(API + `/sessions/${active.id}/runs/${runId}/question`);
      setActive(response.data);
      await reloadListOnly("active");
      messageApi.success("已撤回本次问题");
    } catch (cause) { setError(errorMessage(cause)); }
  };

  const runOptions = (active?.messages || []).filter((item: any) => item.role === "user" && item.run_id)
    .map((item: any) => ({ value: item.run_id, label: item.content.slice(0, 25) }));
  return <>
    {messageContext}{modalContext}
    <Heading title="知识数据推理 · Agent" description="基于知识资料与 WeKnora 检索证据进行推理；会话、反馈、分枝和运行记录均可回看。" extra={<Link to="/knowledge/search">直接检索证据</Link>} />
    <LoadError error={error || models.error} />
    {!options.length && <Alert style={{ marginBottom: 16 }} type="info" showIcon message="知识数据推理尚未启用对话模型" description={<span>请在<Link to="/models">模型配置</Link>中添加并启用知识脑对话通道。</span>} />}
    <div className="cognitive-agent-workspace cognitive-agent-enhanced">
      <aside className="conversation-list">
        <div className="cognitive-session-heading"><strong>会话</strong><Tooltip title="新建知识会话"><Button type="text" icon={<PlusOutlined />} disabled={running} onClick={() => void createSession()} aria-label="新建会话" /></Tooltip></div>
        <Segmented block size="small" value={sessionView} onChange={value => void changeView(value as SessionView)} options={[{ label: "会话", value: "active" }, { label: "归档", value: "archived", icon: <InboxOutlined /> }]} />
        {loadingSessions ? <Spin className="cognitive-list-spinner" /> : <List dataSource={sessions} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessionView === "archived" ? "暂无归档会话" : "暂无会话"} /> }} renderItem={item => <List.Item className={`cognitive-session-item ${active?.id === item.id ? "active" : ""}`} onClick={() => void chooseSession(item.id)}>
          <div className="cognitive-session-copy"><div>{item.pinned && <PushpinFilled />}<Typography.Text ellipsis>{item.title}</Typography.Text></div><small>{item.messages?.length || 0} 条消息</small></div>
          <Dropdown trigger={["click"]} menu={{ items: [
            ...(!item.archived ? [{ key: "pin", icon: item.pinned ? <PushpinOutlined /> : <PushpinFilled />, label: item.pinned ? "取消置顶" : "置顶" }] : []),
            { key: "rename", icon: <EditOutlined />, label: "重命名", disabled: running && active?.id === item.id },
            { key: "archive", icon: item.archived ? <UndoOutlined /> : <InboxOutlined />, label: item.archived ? "恢复" : "归档", disabled: running && active?.id === item.id },
            { type: "divider" as const }, { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, disabled: running && active?.id === item.id },
          ], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); if (key === "pin") void togglePin(item); if (key === "rename") startRename(item); if (key === "archive") void toggleArchive(item); if (key === "delete") confirmDelete(item); } }}>
            <Button type="text" size="small" className="cognitive-session-menu" icon={<MoreOutlined />} aria-label="会话操作" onClick={event => event.stopPropagation()} />
          </Dropdown>
        </List.Item>} />}
      </aside>
      <section className="conversation-main">
        <div className="conversation-toolbar"><Space><strong>{active?.title || "新会话"}</strong>{active?.archived && <Tag>已归档</Tag>}</Space><Select style={{ minWidth: 160, maxWidth: 240 }} aria-label="选择知识对话模型" placeholder="选择模型" value={model} disabled={running || active?.archived} onChange={setModel} options={options.map((item: any) => ({ value: item.id, label: item.name }))} /></div>
        <div className="conversation-messages cognitive-bubble-list">
          {!active?.messages?.length && <Empty style={{ marginTop: 60 }} description="从自己的知识资料开始提问" />}
          {(active?.messages || []).map((item: ConversationMessage) => <div key={item.id} className={`cognitive-chat-row ${item.role}`}>
            <div className="cognitive-chat-avatar">{item.role === "user" ? <UserOutlined /> : <RobotOutlined />}</div>
            <article className="cognitive-chat-bubble">
              <strong>{item.role === "user" ? "你" : item.status === "failed" ? "执行未完成" : "知识助手"}</strong>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
              {item.role === "assistant" && <div className="cognitive-message-actions">
                <Space size={2}>
                  <Tooltip title="复制回答"><Button type="text" size="small" icon={<CopyOutlined />} disabled={!item.content} onClick={() => void copyAnswer(item)} aria-label="复制回答" /></Tooltip>
                  <Tooltip title={item.feedback === "up" ? "取消点赞" : "点赞"}><Button type="text" size="small" icon={item.feedback === "up" ? <LikeFilled /> : <LikeOutlined />} aria-label="点赞" aria-pressed={item.feedback === "up"} onClick={() => void setFeedback(item, "up")} /></Tooltip>
                  <Tooltip title={item.feedback === "down" ? "取消点踩" : "点踩"}><Button type="text" size="small" icon={item.feedback === "down" ? <DislikeFilled /> : <DislikeOutlined />} aria-label="点踩" aria-pressed={item.feedback === "down"} onClick={() => void setFeedback(item, "down")} /></Tooltip>
                  <Tooltip title="在新对话中分枝"><Button type="text" size="small" icon={<BranchesOutlined />} loading={branchingMessageId === item.id} disabled={running || active?.archived} aria-label="在新对话中分枝" onClick={() => void branchFrom(item)} /></Tooltip>
                </Space>
                {item.run_id && <Button size="small" type="link" onClick={() => void chooseRun(item.run_id)}>查看运行步骤</Button>}
              </div>}
              {item.evidence && <Collapse items={[{ key: "evidence", label: "查看本次检索依据", children: <Evidence value={item.evidence} /> }]} />}
            </article>
          </div>)}
          {running && <Alert type="info" showIcon message="正在执行，具体步骤和耗时见运行面板" action={<Button size="small" icon={<PauseCircleOutlined />} onClick={() => void pauseRun()}>暂停</Button>} />}
        </div>
        <div className="conversation-composer">
          {active?.archived && <Alert type="info" showIcon message="归档会话只读；恢复后才能继续提问" />}
          <Input.TextArea rows={3} value={input} disabled={running || active?.archived} onChange={event => setInput(event.target.value)} placeholder={active?.archived ? "归档会话只读" : "输入知识问题"} aria-label="知识数据推理问题" />
          <div className="conversation-send"><span className="status-note">依据授权知识回答，保留来源、反馈与执行记录</span><Space>{active?.run?.status === "paused" && <Button danger onClick={() => void retractRun()}>撤回问题</Button>}<Button type="primary" icon={<SendOutlined />} loading={running} disabled={!model || !input.trim() || active?.archived} onClick={() => void send()}>发送</Button></Space></div>
        </div>
      </section>
      <AgentRunPanel brain="knowledge" run={selectedRun || active?.run} runOptions={runOptions} onSelect={chooseRun} />
    </div>
    <Modal title="重命名知识会话" open={!!renameTarget} okText="保存" cancelText="取消" onOk={() => void renameSession()} onCancel={() => setRenameTarget(null)} okButtonProps={{ disabled: !renameValue.trim() }}><Input value={renameValue} maxLength={200} onChange={event => setRenameValue(event.target.value)} onPressEnter={() => void renameSession()} autoFocus /></Modal>
  </>;
}
