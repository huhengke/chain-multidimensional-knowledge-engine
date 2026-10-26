import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, AutoComplete, Button, Card, Collapse, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, Tooltip, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { http, errorMessage } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import SystemParams from "../SystemParams";
import Topology from "../Topology";
import Accounts from './Accounts';
import KnowledgeTree from './KnowledgeTree';
import MemoryTopology from '../../components/MemoryTopology';
import { API, brainName, Heading, Json, LoadError, Refresh, Status, useData } from "./shared";

export function Overview() {
  const { data, error, refresh } = useData("/overview", 10000);
  return <><Heading title="运行总览" description="管理三类数据，查看处理状态，进入各脑工作台。" extra={<Refresh onClick={refresh} />} /><LoadError error={error} /><div className="platform-stack">
    <div className="platform-grid"><Card title="结构化数据推理"><Status value={data?.services?.structured || "unavailable"} /><p>数据源、字段画像、基座特征、精确聚合与智慧树。</p><Space wrap><Link to="/datasets">数据集与数据浏览</Link><Link to="/agent">统一 Agent</Link></Space></Card>
      <Card title="知识数据推理"><Tag color="blue">WeKnora 内嵌运行时</Tag><p>资料导入、父子分块、混合检索、原文证据与自动知识星团。</p><Space wrap><Link to="/intake">导入资料</Link><Link to="/agent">统一 Agent</Link><Link to="/knowledge/search">证据检索</Link></Space></Card>
      <Card title="时空记忆推理"><Tag color="blue">文件与实时流接入</Tag><p>媒体资产、时间与地点记录、采样帧和记忆星团。</p><Space wrap><Link to="/streams">实时采集</Link><Link to="/agent">统一 Agent</Link><Link to="/memory/search">时空检索</Link></Space></Card></div>
    <div className="platform-grid"><Card title="我的知识与时空来源"><div className="metric-number">{data?.sources ?? "—"}</div><Link to="/intake">来源与数据接入</Link></Card><Card title="我的已接入资料"><div className="metric-number">{data?.assets ?? "—"}</div><Link to="/jobs">导入任务与资料详情</Link></Card><Card title="开始接入"><p>登记来源、注册数据集并提交资料，摄像头与 RTSP 从这里登记。</p><Link to="/intake"><Button type="primary">数据接入</Button></Link></Card></div>
    <Alert showIcon type={data?.knowledge_models?.embedding && data?.knowledge_models?.rerank ? "info" : "warning"} message="知识脑使用本项目内嵌的 WeKnora 派生运行时" description={`向量模型：${data?.knowledge_models?.embedding ? '已启用' : '待配置'}；重排模型：${data?.knowledge_models?.rerank ? '已启用' : '待配置'}；图谱与对话模型：${data?.knowledge_models?.chat ? '已启用' : '待配置'}。模型和设备的真实效果以导入、检索任务结果为准。`} />
    <Card title="最近知识与时空导入任务" extra={<Link to="/jobs">查看全部</Link>}><Table<Record<string, any>> rowKey="id" dataSource={data?.jobs || []} pagination={false} columns={[{ title: "资料", dataIndex: "title" }, { title: "所属脑", dataIndex: "brain", render: v => brainName[v] }, { title: "状态", dataIndex: "status", render: v => <Status value={v} /> }, { title: "说明", dataIndex: "error" }]} /></Card>
  </div></>;
}

export function ModelCenter() {
  const { data, error, refresh } = useData("/models");
  const { user } = useAuth();
  const [edit, setEdit] = useState<any>(null);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [checking, setChecking] = useState('');
  const [checkResult, setCheckResult] = useState<{ok:boolean;message:string} | null>(null);
  const save = async (v: any) => { setSaving(true); try { await http.put(API + `/models/${edit.id || "new"}`, v); setEdit(null); await refresh(); message.success("模型配置已保存"); } catch (e) { message.error(errorMessage(e)); } finally { setSaving(false); } };
  const check = async (id: string, action='check') => { setChecking(id+action); try { const r = await http.post(API + `/models/${id}/${action}`); setCheckResult(r.data); } catch (e) { setCheckResult({ok:false,message:errorMessage(e)}); } finally {setChecking('');} };
  const discover = async () => { try { await form.validateFields(['base_url']); setDiscovering(true); const r=await http.post(API+'/models/discover',{base_url:form.getFieldValue('base_url'),api_key:form.getFieldValue('api_key')||'',model_id:edit.id||null}); setModelOptions(r.data.model_ids||[]); message[r.data.ok?'success':'error'](r.data.message); } catch(e) {message.error(errorMessage(e));} finally {setDiscovering(false);} };
  const open = (row: any) => { setEdit(row); setModelOptions([]); form.resetFields(); form.setFieldsValue({ name: row.name || "", brain: row.brain || "knowledge", purpose: row.purpose || "chat", base_url: row.base_url || "", model: row.model || "", enabled: row.enabled || false, supports_images: row.supports_images || false, supports_stream: row.supports_stream !== false, timeout: row.timeout || 90, deployment: row.deployment || "", deployment_name: row.deployment_name || "", is_default: Boolean(row.is_default), api_key: "" }); };
  const seconds = (value?: number | null) => value == null ? "暂无" : `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} 秒`;
  const stats = (row: any) => row.inference_stats || {};
  return <><Heading title="模型配置" description="管理模型地址、用途、启用状态与凭据；各脑按用途选择模型。" /><LoadError error={error} />{checkResult&&<Alert style={{marginBottom:16}} showIcon closable onClose={()=>setCheckResult(null)} type={checkResult.ok?'success':'error'} message={checkResult.message}/>}<Alert style={{marginBottom:16}} type="info" showIcon message="最近推理统计" description="按该模型部署最近 50 次平台 Agent 实际调用统计；连接检查和读取模型列表不计入。调用耗时单独统计检索前置、模型往返、首 token 与流式生成，避免把排队时间误判成生成速度。" /><Tabs items={[
    { key: "new", label: "编排与三脑模型", children: <Card extra={<Button icon={<PlusOutlined />} type="primary" disabled={user?.role !== "admin"} onClick={() => open({})}>添加模型</Button>}><Table<Record<string, any>> rowKey="id" dataSource={data?.items || []} columns={[
      { title: "名称", dataIndex: "name" }, { title: "所属脑", dataIndex: "brain", render: v => brainName[v] }, { title: "用途", dataIndex: "purpose" }, { title: "模型", dataIndex: "model", render: (value, row) => <Space size={4} wrap><span>{value}</span>{row.deployment && <Tag color={row.is_default ? "blue" : "default"}>{row.deployment_name || row.deployment}{row.is_default ? " · 默认" : ""}</Tag>}</Space> }, { title: "状态", render: (_, row) => <Tag color={row.enabled ? "green" : "default"}>{row.enabled ? "已启用" : "未启用"}</Tag> }, { title: "最近推理", render: (_, row) => { const value=stats(row); return value.attempt_count ? <Tooltip title={`最近调用：${value.latest_at ? new Date(value.latest_at).toLocaleString() : '未知'}；模型往返 P95：${seconds(value.p95_duration_ms)}；检索平均：${seconds(value.average_retrieval_duration_ms)}；首 token P95：${seconds(value.p95_ttft_ms)}`}><span>{value.attempt_count} 次 · 模型平均 {seconds(value.average_duration_ms)}</span></Tooltip> : <span>暂无运行记录</span>; } }, { title: "生成耗时", render: (_, row) => { const value=stats(row); return value.generation_count ? <Tooltip title={`流式生成 P95：${seconds(value.p95_generation_duration_ms)}；已采集 ${value.generation_count} 次`}><span>平均 {seconds(value.average_generation_duration_ms)}</span></Tooltip> : <span>未采集</span>; } }, { title: "超时统计", render: (_, row) => { const value=stats(row); return value.attempt_count ? <Tag color={value.timeout_count ? "red" : "green"}>{value.timeout_count} 次 · {value.timeout_rate}%</Tag> : <span>—</span>; } }, { title: "凭据", dataIndex: "key_configured", render: v => v ? "已加密保存" : "未设置" }, { title: "操作", render: (_, row) => <Space wrap><Button disabled={user?.role !== "admin"} onClick={() => open(row)}>编辑</Button><Button disabled={user?.role !== "admin" || !!checking} loading={checking===row.id+'check'} onClick={() => check(row.id)}>检查连接与模型</Button>{['chat','embedding','rerank'].includes(row.purpose)&&<Button disabled={user?.role!=='admin'||!!checking} loading={checking===row.id+'probe'} onClick={()=>check(row.id,'probe')}>验证推理</Button>}</Space> },
    ]} /></Card> },
  ]} /><Modal open={!!edit} title="模型配置" onCancel={() => setEdit(null)} footer={null}><Form layout="vertical" form={form} onFinish={save} onValuesChange={changed=>{if('base_url' in changed || 'api_key' in changed) setModelOptions([]);}}>
    <Form.Item name="name" label="配置名称" rules={[{ required: true }]} extra="便于识别的别名，例如公司的模型服务；实际调用使用下面的模型 ID。"><Input /></Form.Item>
    <Space wrap><Form.Item name="brain" label="所属脑或控制面"><Select style={{ width: 185 }} options={[{ value: "orchestrator", label: "统一编排控制面" }, { value: "structured", label: "结构化数据推理脑" }, { value: "knowledge", label: "知识数据推理脑" }, { value: "memory", label: "时空记忆推理脑" }]} /></Form.Item><Form.Item name="purpose" label="用途"><Select style={{ width: 185 }} options={[{ value: "chat", label: "Agent 对话 / 结果合一" }, { value: "vision", label: "视觉提取" }, { value: "asr", label: "音频转写" }, { value: "embedding", label: "向量化" }, { value: "rerank", label: "检索重排" }]} /></Form.Item></Space>
    <Form.Item noStyle shouldUpdate={(prev, next) => prev.purpose !== next.purpose}>
      {({ getFieldValue }) => getFieldValue("purpose") === "chat" ? <Space wrap>
        <Form.Item name="deployment" label="部署标识" extra="Agent 模型切换依据，例如 codex；同组模型须使用相同标识"><Input placeholder="codex" /></Form.Item>
        <Form.Item name="deployment_name" label="部署显示名"><Input placeholder="Codex" /></Form.Item>
        <Form.Item name="is_default" label="默认部署" valuePropName="checked" extra="每个脑仅一个默认对话模型"><Switch /></Form.Item>
      </Space> : null}
    </Form.Item>
    <Form.Item name="base_url" label="API Base URL" rules={[{ required: true }]} extra="填写 OpenAI 兼容接口根地址，按服务商要求包含 /v1。"><Input placeholder="https://provider.example/v1" /></Form.Item>
    <Form.Item name="api_key" label="API Key" extra="留空保留现有凭据；密钥不会回显。"><Input.Password autoComplete="new-password" /></Form.Item>
    <Button style={{marginBottom:12}} loading={discovering} onClick={()=>void discover()}>读取可用模型</Button>
    <Form.Item name="model" label="模型 ID" rules={[{ required: true },{validator:(_,value)=>modelOptions.length&&!modelOptions.includes(value)?Promise.reject(new Error('此 ID 不在刚读取的可用模型列表中，请重新选择')):Promise.resolve()}]} extra="从服务端列表选择或填写准确 ID，例如 gpt-5.6-sol；不要填写 OpenAI 等厂商名。"><AutoComplete options={modelOptions.map(value=>({value}))} filterOption={(input,option)=>String(option?.value).toLowerCase().includes(input.toLowerCase())} placeholder="先填写地址与凭据，再读取可用模型"/></Form.Item>
    <Form.Item name="supports_images" label="对话模型支持图片输入" valuePropName="checked" extra="仅在模型确实支持图片输入时开启，供结构化 Agent 附件使用。"><Switch /></Form.Item>
    <Form.Item name="supports_stream" label="启用流式输出与生成计时" valuePropName="checked" extra="模型支持 OpenAI 兼容 SSE 时开启，可记录首 token 与实际生成耗时；不支持的服务请关闭。"><Switch /></Form.Item>
    <Alert type="info" showIcon style={{ marginBottom: 16 }} message="统一编排控制面只负责合并三脑已核验结果；知识脑导入使用向量模型，检索使用重排模型，各脑分别使用自己的对话模型。" />
    <Space><Form.Item name="timeout" label="超时（秒）"><InputNumber min={5} max={300} /></Form.Item><Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item></Space>
    <Button htmlType="submit" type="primary" loading={saving}>保存配置</Button>
  </Form></Modal></>;
}

export function SettingsCenter() {
  const { data, error } = useData("/settings");
  const { user } = useAuth();
  const [form] = Form.useForm();
  useEffect(() => { if (data) form.setFieldsValue(data); }, [data, form]);
  const save = async (v: any) => { try { await http.put(API + "/settings", v); message.success("系统设置已保存"); } catch (e) { message.error(errorMessage(e)); } };
  return <><Heading title="系统设置" description="配置平台上传、登录和采集限制；结构化引擎参数保留独立版本与生效规则。" /><LoadError error={error} /><Tabs items={[
    { key: "platform", label: "平台参数", children: <Card><Form form={form} layout="vertical" onFinish={save} style={{ maxWidth: 640 }} disabled={user?.role !== "admin"}><Form.Item name="upload_limit_mb" label="单文件上传上限（MB）"><InputNumber min={1} max={100} /></Form.Item><Form.Item name="session_hours" label="新登录会话有效时长（小时）"><InputNumber min={1} max={24} /></Form.Item><Form.Item name="stream_frame_limit" label="每次实时采集最大保存帧数" extra="达到上限后自动停止；已保存帧进入资产目录。"><InputNumber min={10} max={3000} /></Form.Item><Button type="primary" htmlType="submit">保存参数</Button></Form></Card> },
    { key: "engine", label: "结构化引擎参数", children: <SystemParams /> },
    ...(user?.role === 'admin' ? [{key:'accounts',label:'账号与角色',children:<Accounts/>}] : []),
  ]} /></>;
}

export function AuditCenter() {
  const { data, error, refresh } = useData("/audit", 5000);
  return <><Heading title="操作审计" description="记录登录、来源登记、配置变更、导入、查询和模型调用结果。" extra={<Refresh onClick={refresh} />} /><LoadError error={error} /><Card><Table<Record<string, any>> rowKey="seq" dataSource={data?.items || []} columns={[{ title: "时间", dataIndex: "at", render: v => new Date(v).toLocaleString() }, { title: "操作人", dataIndex: "owner" }, { title: "操作", dataIndex: "action" }, { title: "对象", dataIndex: "target", ellipsis: true }]} expandable={{ expandedRowRender: row => <Json value={row.detail} /> }} /></Card></>;
}

export function StreamCenter() {
  const sources = useData("/sources");
  const sessions = useData("/streams", 3000);
  const { user } = useAuth();
  const [busy, setBusy] = useState("");
  const start = async (id: string) => { setBusy(id); try { await http.post(API + `/sources/${id}/start`); await sessions.refresh(); message.success("已提交采集启动请求"); } catch (e) { message.error(errorMessage(e)); } finally { setBusy(""); } };
  const stop = async (id: string) => { try { await http.post(API + `/streams/${id}/stop`); await sessions.refresh(); } catch (e) { message.error(errorMessage(e)); } };
  return <><Heading title="实时采集" description="手动启动已登记摄像头或 RTSP 来源，采样帧持久保存为媒体资产。" extra={<Link to="/intake?brain=memory"><Button>登记设备来源</Button></Link>} /><LoadError error={sources.error || sessions.error} /><div className="platform-stack"><Card title="可用来源"><Table<Record<string, any>> rowKey="id" dataSource={(sources.data?.items || []).filter((s: any) => ["rtsp", "camera"].includes(s.kind))} columns={[{ title: "名称", dataIndex: "name" }, { title: "类型", dataIndex: "kind" }, { title: "采样间隔", dataIndex: "sample_interval", render: v => `${v} 秒` }, { title: "操作", render: (_, row) => <Button type="primary" disabled={user?.role === "viewer"} loading={busy === row.id} onClick={() => start(row.id)}>启动采集</Button> }]} /></Card>
    <Card title="采集会话" extra={<Link to="/jobs?brain=memory">查看采样资料与任务</Link>}><Table<Record<string, any>> rowKey="id" dataSource={sessions.data?.items || []} columns={[{ title: "来源", dataIndex: "source_summary" }, { title: "状态", dataIndex: "status", render: v => <Status value={v} /> }, { title: "已采集帧", dataIndex: "frames_captured" }, { title: "错误说明", dataIndex: "last_error_message" }, { title: "操作", render: (_, row) => ["running", "connecting", "degraded"].includes(row.status) ? <Button danger disabled={user?.role === "viewer"} onClick={() => stop(row.id)}>停止采集</Button> : null }]} /></Card></div></>;
}

export function TopologyCenter() {
  const [clusterPage, setClusterPage] = useState(1);
  const [linkPage, setLinkPage] = useState(1);
  const { data, error, refresh } = useData(`/topology?cluster_page=${clusterPage}&link_page=${linkPage}&page_size=20`, 10000);
  const [params, setParams] = useSearchParams();
  return <><Heading title="智慧树与星团" description="各脑保留独立结构；跨脑关联使用公共锚点，并由目标脑核验证据。" extra={<Refresh onClick={refresh} />} /><LoadError error={error} /><Tabs activeKey={params.get('brain') || 'structured'} onChange={key => setParams({brain:key})} items={[
    { key: "structured", label: "结构化智慧树", children: <Topology /> },
    { key: "knowledge", label: "知识智慧树与星团", children: <div className="platform-stack"><KnowledgeTree value={data?.knowledge_topology}/><Card title={`知识星团（${data?.knowledge_cluster_total ?? 0}）`}><Table<Record<string, any>> rowKey="cluster_id" dataSource={data?.knowledge_clusters || []} pagination={{current:clusterPage,pageSize:20,total:data?.knowledge_cluster_total || 0,showSizeChanger:false,onChange:setClusterPage}} columns={[{ title: "星团", dataIndex: "cluster_id" }, { title: "知识树", dataIndex: "tree_id" }, { title: "独立来源", dataIndex: "independent_source_count" }, { title: "次数", dataIndex: "occurrence_count" }, { title: "状态", dataIndex: "status", render: v => <Status value={v} /> }]} expandable={{ expandedRowRender: row => <Json value={row} /> }} /></Card><Card title={`知识时间维度星团（${data?.knowledge_temporal_cluster_total ?? 0}）`} extra={<Tag color="blue">内容时间索引</Tag>}><p className="status-note">内容时间与资料导入/观察时间分开存储；“去年发生了什么”走这里，“我最近看了什么”走中通时间索引，双时间问题由系统自动求交。</p><Table<Record<string, any>> rowKey="temporal_cluster_id" dataSource={data?.knowledge_temporal_clusters || []} pagination={false} columns={[{ title: "时间星团", dataIndex: "temporal_cluster_id" }, { title: "知识树", dataIndex: "tree_id" }, { title: "内容时间", render: (_, row) => `${row.start} ～ ${row.end}` }, { title: "知识单元", dataIndex: "member_count" }, { title: "资料数", dataIndex: "document_count" }, { title: "粒度", dataIndex: "granularity" }]} expandable={{ expandedRowRender: row => <Json value={row} /> }} /></Card></div> },
    { key: "memory", label: "时空星团与记忆单元", children: <div className="platform-stack"><MemoryTopology value={data} /><Card title={`记忆单元明细（${data?.memory_units?.length ?? 0}）`}><Table<Record<string, any>> rowKey="unit_id" dataSource={data?.memory_units || []} columns={[{ title: "日期", dataIndex: "day_key" }, { title: "记忆树", dataIndex: "tree_id" }, { title: "状态", dataIndex: "status", render: v => <Status value={v} /> }, { title: "来源", dataIndex: "source_ref" }]} expandable={{ expandedRowRender: row => <Json value={row} /> }} /><Collapse items={[{ key: "clusters", label: "查看自动记忆星团", children: <Json value={data?.memory_clusters || []} /> }]} /></Card></div> },
    { key: "links", label: "跨脑关联", children: <Card title={`跨脑关联（${data?.cross_brain_link_total ?? 0}）`}><Table<Record<string, any>> rowKey="link_id" dataSource={data?.cross_brain_links || []} pagination={{current:linkPage,pageSize:20,total:data?.cross_brain_link_total || 0,showSizeChanger:false,onChange:setLinkPage}} columns={[{ title: "源", dataIndex: "source_ref" }, { title: "目标脑", dataIndex: "target_brain", render: v => brainName[v] }, { title: "锚点", dataIndex: "anchor_value" }, { title: "依据", dataIndex: "basis" }, { title: "状态", dataIndex: "status", render: v => <Status value={v} /> }]} /></Card> },
  ]} /></>;
}
