import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Collapse, Descriptions, Drawer, Form, Input, InputNumber, Modal, Select, Space, Table, Tabs, Tag, message } from "antd";
import { PlusOutlined, UploadOutlined } from "@ant-design/icons";
import { http, errorMessage } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { API, brainName, Heading, Json, LoadError, Refresh, useData } from "./shared";
import { StructuredImportTasks } from './StructuredImportTasks';
import { mergeImportRows } from './import-rows';
import { submitUploadBatch, uploadIssue, type UploadEntry } from "./upload-batch";
import { ClassificationDetail, ClassificationEditor, ClassificationTag } from './KnowledgeClassification';
import { ProcessingStatus, PublicationStatus, PublicationManager, publicationActionName } from './KnowledgePublication';
import Datasets from '../Datasets';

const sourceKindName: Record<string, string> = {file:'文件导入',text:'文本资料',camera:'本机摄像头',rtsp:'RTSP 视频流'};

function SourceRegistry({ brain, sources, onSelect, disabled }: { brain: string; sources: ReturnType<typeof useData>; onSelect: (source: any) => void; disabled: boolean }) {
  const { data, error, loading, refresh } = sources;
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const kind = Form.useWatch("kind", form);
  const save = async (body: any) => { setBusy(true); try { const response = await http.post(API + "/sources", {...body, brain}); setOpen(false); form.resetFields(); await refresh(); if (['file', 'text'].includes(response.data.kind)) onSelect(response.data); message.success("来源已登记，可在下方继续导入"); } catch (e) { message.error(errorMessage(e)); } finally { setBusy(false); } };
  return <><LoadError error={error} /><Card id="source-registry" title={brain === 'knowledge' ? '知识资料来源' : '媒体与设备来源'} extra={<Space wrap><Refresh onClick={refresh} /><Button type="primary" icon={<PlusOutlined />} disabled={disabled || user?.role === 'viewer'} onClick={() => { form.resetFields(); setOpen(true); }}>登记{brain === 'knowledge' ? '知识' : '时空'}来源</Button></Space>}>
    <p>{brain === 'knowledge' ? '管理用于知识解析、检索和智慧树生长的文件及文本来源。' : '管理图片、音视频、时空记录，以及摄像头和 RTSP 实时流来源。'}</p>
    <Table<Record<string, any>> rowKey="id" loading={loading} scroll={{x:640}} dataSource={(data?.items || []).filter((s: any) => s.brain === brain)} locale={{emptyText: brain === 'knowledge' ? '尚未登记知识数据来源' : '尚未登记时空数据来源'}} columns={[{ title: '名称', dataIndex: 'name' }, { title: '接入方式', dataIndex: 'kind', render: v => sourceKindName[v] || v }, { title: '来源摘要', dataIndex: 'summary' }, { title: '操作', render: (_, row) => row.kind === 'rtsp' || row.kind === 'camera' ? <Link to="/streams">前往实时采集</Link> : <Button type="link" disabled={disabled} onClick={() => onSelect(row)}>选用并导入</Button> }]} />
  </Card>
    <Modal title={brain === 'knowledge' ? '登记知识数据来源' : '登记时空数据来源'} open={open} footer={null} onCancel={() => setOpen(false)} destroyOnClose><Form form={form} layout="vertical" initialValues={{ kind: "file", sample_interval: 5, device_index: 0 }} onFinish={save}>
      <Form.Item name="name" label="来源名称" rules={[{ required: true }]}><Input maxLength={200} /></Form.Item>
      <Form.Item name="kind" label="接入方式"><Select options={[{ value: "file", label: "文件导入" }, { value: "text", label: "文本资料" }, ...(brain === "memory" ? [{ value: "camera", label: "本机摄像头" }, { value: "rtsp", label: "RTSP 视频流" }] : [])]} /></Form.Item>
      {kind === "rtsp" && <Form.Item name="rtsp_url" label="RTSP 地址" rules={[{ required: true }]} extra="凭据加密保存在服务端；页面仅展示脱敏摘要。"><Input.Password autoComplete="new-password" placeholder="rtsp://…" /></Form.Item>}
      {kind === "camera" && <Form.Item name="device_index" label="摄像头设备编号"><InputNumber min={0} max={32} /></Form.Item>}
      {["camera", "rtsp"].includes(kind) && <Form.Item name="sample_interval" label="采样间隔（秒）"><InputNumber min={1} max={60} /></Form.Item>}
      <Button type="primary" htmlType="submit" loading={busy}>保存来源</Button>
    </Form></Modal>
  </>;
}

export function IntakeCenter() {
  const navigate = useNavigate();
  const sources = useData("/sources");
  const { data, error } = sources;
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const brain = ['structured', 'knowledge', 'memory'].includes(params.get('brain') || '') ? params.get('brain')! : 'knowledge';
  const [mode, setMode] = useState("file");
  const [files, setFiles] = useState<UploadEntry[]>([]);
  const limits = useData("/imports/limits");
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const selectSource = (source: any) => {
    form.setFieldValue('source_id', source.id); setMode(source.kind);
    setParams({brain, source: source.id}, {replace:true});
    document.getElementById('intake-form')?.scrollIntoView({behavior:'smooth', block:'start'});
  };
  useEffect(() => { form.resetFields(); setMode('file'); setFiles([]); }, [brain, form]);
  useEffect(() => {
    const source = (data?.items || []).find((s: any) => s.id === params.get('source') && s.brain === brain && ['file','text'].includes(s.kind));
    if (source) { form.setFieldValue('source_id', source.id); setMode(source.kind); }
  }, [data, params, brain, form]);
  const submit = async (v: any) => {
    setBusy(true);
    try {
      if (mode === "text") {
        await http.post(API + "/imports/text", v);
        message.success("已创建导入任务，原始资料已保存"); navigate(`/jobs?brain=${brain}`);
      }
      else {
        if (!files.length) { message.warning("请选择文件"); return; }
        // Re-read the current policy before each submission, including retries
        // after an administrator has adjusted the limit in another tab.
        const currentLimits = (await http.get(API + "/imports/limits")).data;
        await limits.refresh();
        const result = await submitUploadBatch(files, currentLimits.per_file_bytes, async file => {
          const body = new FormData(); body.append("file", file); body.append("source_id", v.source_id);
          for (const key of ["timestamp", "place", "person", "description"]) if (v[key]) body.append(key, v[key]);
          const response = await http.post(API + "/imports/files", body);
          return response.data.id;
        }, entry => setFiles(previous => previous.map(item => item.id === entry.id ? entry : item)), errorMessage);
        const submitted = result.filter(item => item.status === "submitted").length;
        const remaining = result.length - submitted;
        if (remaining) message.warning(`已提交 ${submitted} 个，未提交 ${remaining} 个；请查看逐文件原因。`);
        else message.success(`已提交 ${submitted} 个文件；解析与索引结果请到导入任务查看。`);
      }
    } catch (e) { message.error(errorMessage(e)); } finally { setBusy(false); }
  };
  return <><Heading title="数据接入" description="统一登记知识、时空和结构化数据来源。知识与时空资料在本页直接导入；结构化数据页签复用数据源、数据集、智慧树和导入工作区。" extra={<Link to={`/jobs?brain=${brain}`}><Button>导入任务</Button></Link>} /><LoadError error={error} />
    <Tabs destroyInactiveTabPane activeKey={brain} onChange={value => { if (!busy) setParams({brain:value}); }} items={[
      ...["knowledge", "memory"].map(b => ({ key: b, label: b === 'knowledge' ? '知识数据' : '时空数据', children: <div className="platform-stack"><SourceRegistry key={b} brain={b} sources={sources} onSelect={selectSource} disabled={busy} /><Card id="intake-form" title={b === "knowledge" ? "导入知识资料" : "导入媒体与时空记录"}>
        <Form form={form} layout="vertical" onFinish={submit} style={{ maxWidth: 850 }} disabled={user?.role === "viewer" || busy}>
          <Form.Item name="source_id" label="来源" rules={[{ required: true, message: "请先在上方登记并选择来源" }]}><Select placeholder="选择已登记来源" onChange={id => { const source = data?.items?.find((s: any) => s.id === id); if (source) selectSource(source); }} options={(data?.items || []).filter((s: any) => s.brain === b && ["file", "text"].includes(s.kind)).map((s: any) => ({ value: s.id, label: s.name }))} notFoundContent="请先在上方登记来源" /></Form.Item>
          <Form.Item label="提交方式"><Select value={mode} onChange={setMode} options={[{ value: "file", label: "上传文件" }, { value: "text", label: "粘贴文本" }]} /></Form.Item>
          {mode === "file" ? <>
            <LoadError error={limits.error} />
            <Alert type="info" showIcon style={{marginBottom:16}} message={limits.data ? `单文件上限 ${limits.data.per_file_mb} MB，逐个校验，不按整批合计大小限制` : "正在读取上传限制…"}
              description={<Space wrap><span>超限文件会跳过，其余文件继续；提交成功不代表解析和索引已完成。</span><Button size="small" onClick={() => void limits.refresh()}>刷新限制</Button>{user?.role === "admin" && <Link to="/settings" target="_blank">调整上传上限</Link>}</Space>} />
            <Form.Item label="原始文件" extra={b === "knowledge" ? "TXT、Markdown、PDF、DOCX、CSV、JSON；PDF 无文本的页面自动使用本地 OCR（含繁体竖排）。最多 500 页，进度见导入任务，原件和页码保留。图片需配置视觉模型。" : "图片、视频、音频或文本；可提取能力取决于已配置模型。"}>
              <input aria-label="选择导入文件" type="file" multiple disabled={busy || user?.role === "viewer"} accept={b === "knowledge" ? ".txt,.md,.pdf,.docx,.csv,.json,.png,.jpg,.jpeg,.webp,.mp3,.wav" : "image/*,video/*,audio/*,.txt,.md"} onChange={e => setFiles(Array.from(e.target.files || []).map(file => ({id:crypto.randomUUID(), file, status:'pending'})))} />
            </Form.Item>
            {files.length > 0 && <><p>已选择 {files.length} 个文件，合计 {(files.reduce((sum, entry) => sum + entry.file.size, 0) / 1024 / 1024).toFixed(2)} MB；已提交 {files.filter(entry => entry.status === 'submitted').length} 个。</p>
              <Table<UploadEntry> size="small" rowKey="id" dataSource={files} pagination={false} style={{marginBottom:16}} columns={[
                {title:'文件',render:(_, entry) => <span style={{overflowWrap:'anywhere'}}>{entry.file.name}</span>},
                {title:'大小',width:105,render:(_,entry) => `${(entry.file.size / 1024 / 1024).toFixed(2)} MB`},
                {title:'提交状态',render:(_,entry) => {
                  const issue = entry.status !== 'submitted' && limits.data ? uploadIssue(entry.file, limits.data.per_file_bytes) : undefined;
                  const status = {pending:'待提交',uploading:'上传中',submitted:'已创建任务',failed:'提交失败',rejected:'未提交'}[entry.status];
                  return <Space direction="vertical" size={0}><Tag color={entry.status === 'submitted' ? 'green' : issue || ['failed','rejected'].includes(entry.status) ? 'red' : 'blue'}>{issue ? '不符合限制' : status}</Tag>{(issue || entry.error) && <span>{issue || entry.error}</span>}{entry.jobId && <Link to={`/jobs?brain=${b}`}>查看导入任务</Link>}</Space>;
                }},
              ]} /></>}
          </> : <><Form.Item name="title" label="资料标题" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="content" label="资料正文" rules={[{ required: true }]}><Input.TextArea rows={10} /></Form.Item></>}
          {b === "memory" && <><Form.Item name="timestamp" label="实际发生时间" extra="未知时间可留空；系统不会用上传时间替代事件时间。"><Input type="datetime-local" /></Form.Item><Space wrap><Form.Item name="place" label="地点"><Input /></Form.Item><Form.Item name="person" label="人物/参与主体"><Input /></Form.Item></Space>{mode === "file" && <Form.Item name="description" label="资料说明"><Input.TextArea rows={3} /></Form.Item>}</>}
          <Space wrap><Button type="primary" htmlType="submit" icon={<UploadOutlined />} loading={busy} disabled={mode === 'file' && (!limits.data || files.length > 0 && files.every(entry => entry.status === 'submitted'))}>{files.some(entry => entry.status === 'submitted' || entry.status === 'failed' || entry.status === 'rejected') && mode === 'file' ? '重试未提交文件' : '开始导入'}</Button><Link to={`/jobs?brain=${b}`}>查看导入任务</Link>{b === "memory" && <Link to="/streams">实时摄像头 / RTSP 接入</Link>}</Space>
        </Form>
      </Card></div> })),
      { key: 'structured', label: '结构化数据', children: <Datasets /> },
    ]} />
  </>;
}

export function JobCenter() {
  const [params, setParams] = useSearchParams();
  const brain = ['structured', 'knowledge', 'memory'].includes(params.get('brain') || '') ? params.get('brain')! : 'knowledge';
  const intakeTarget = `/intake?brain=${brain}`;
  return <><Heading title="导入任务" description="按资料查看处理进度、导入结果与原件；知识资料的分类和发布管理也在这里完成。" extra={<Link to={intakeTarget}><Button type="primary" icon={<PlusOutlined />}>接入数据</Button></Link>} />
    <Tabs destroyInactiveTabPane activeKey={brain} onChange={value => setParams({brain:value})} items={[
      {key:'structured', label:'结构化数据', children:<StructuredImportTasks />},
      {key:'knowledge', label:'知识数据', children:<AssetImportTasks key="knowledge" brain="knowledge" />},
      {key:'memory', label:'时空数据', children:<AssetImportTasks key="memory" brain="memory" />},
    ]} />
  </>;
}

function AssetImportTasks({ brain }: { brain: string }) {
  const { data, error, loading, refresh } = useData("/assets", 6000);
  const jobs = useData('/jobs', 4000);
  const { user } = useAuth();
  const [detail, setDetail] = useState<any>(null);
  const [review, setReview] = useState<any>(null);
  const [classificationAsset, setClassificationAsset] = useState<any>(null);
  const [taskRowId, setTaskRowId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [state, setState] = useState('all');
  const [retrying, setRetrying] = useState('');
  const rows = mergeImportRows(data?.items || [], jobs.data?.items || []).filter(row => row.brain === brain);
  const visible = rows.filter(row => row.title.toLowerCase().includes(query.trim().toLowerCase()) && (state === 'all' || row.processing_status === state));
  const selected = rows.find(row => row.id === taskRowId);
  const refreshAll = async () => { await Promise.all([refresh(), jobs.refresh()]); };
  const retry = async (id: string) => { setRetrying(id); try { await http.post(API + `/jobs/${id}/retry`); await refreshAll(); message.success('已重新提交处理任务，原有发布记录保留'); } catch (e) { message.error(errorMessage(e)); } finally { setRetrying(''); } };
  const open = async (id: string) => { try { setDetail((await http.get(API + `/assets/${id}`)).data); } catch (e) { message.error(errorMessage(e)); } };
  return <><LoadError error={error || jobs.error} /><Card title={`${brain === 'knowledge' ? '知识' : '时空'}导入记录（${rows.length} 份资料）`} extra={<Refresh onClick={refreshAll} />}>
    <Space wrap style={{marginBottom:16}}><Input aria-label="搜索导入资料" placeholder="搜索资料名称" allowClear value={query} onChange={e => setQuery(e.target.value)} style={{width:240}} /><Select aria-label="筛选处理状态" value={state} onChange={setState} style={{width:160}} options={[{value:'all',label:'全部处理状态'}, {value:'queued',label:'排队中'}, {value:'running',label:'处理中'}, {value:'completed',label:'已完成'}, {value:'partial',label:'部分完成'}, {value:'failed',label:'失败'}, {value:'cancelled',label:'已取消'}]} /></Space>
    <Table<Record<string, any>> rowKey="id" loading={loading || jobs.loading} scroll={{ x: 1300 }} dataSource={visible} pagination={{pageSize:10, showTotal:total => `共 ${total} 份资料`}} columns={[
    { title: "资料", dataIndex: "title", width: 240, render:(title, row) => <Space direction="vertical" size={2}><span style={{overflowWrap:'anywhere'}}>{title}</span><small>{row.asset ? `${(row.bytes / 1024).toFixed(1)} KB` : '原件信息暂不可用'}</small>{brain === 'knowledge' && <ClassificationTag value={row.classification} />}</Space> },
    { title: "解析与索引", dataIndex: "processing_status", width: 115, render: v => <ProcessingStatus value={v} /> },
    ...(brain === 'knowledge' ? [{ title: "发布状态", dataIndex: "publication_status", width: 120, render: (v:string) => <PublicationStatus value={v} /> }] : []),
    {title:'尝试次数', width:90, render:(_, row) => row.job?.attempts ?? '—'},
    {title:'说明 / 进度', width:370, render:(_, row) => <Space direction="vertical" size={4}>{row.job?.error ? <span style={{color:'#a32c2c'}}>{row.job.error}</span> : row.job?.progress?.message && <span>{row.job.progress.message}</span>}{row.processing_status === 'partial' && <span>本次处理已结束，可查看详情后按需重新处理。</span>}{!row.job && <span>资料已保存，暂无独立导入任务记录</span>}</Space>},
    { title: "操作", width: 365, render: (_, row) => <Space wrap><Button size="small" onClick={() => setTaskRowId(row.id)}>任务详情</Button>{row.asset && <><Button size="small" onClick={() => open(row.id)}>资料详情</Button><a href={API + `/assets/${row.id}/content`}>下载原件</a></>}{row.job && ['failed','partial','cancelled'].includes(row.processing_status) && ['admin','operator'].includes(user?.role || '') && <Button size="small" loading={retrying === row.job.id} disabled={!!retrying} onClick={() => retry(row.job.id)}>重新处理</Button>}{row.asset && row.brain === 'knowledge' && row.document_id && !['queued','running'].includes(row.processing_status) && <>{['admin', 'operator'].includes(user?.role || '') && <Button size="small" onClick={() => setClassificationAsset(row)}>分类纠偏</Button>}<Button size="small" onClick={() => setReview(row)}>{user?.role === 'admin' ? publicationActionName(row.publication_status) : '查看发布记录'}</Button></>}</Space> },
  ]} /></Card><Drawer width={720} title={`任务详情 · ${selected?.title || ''}`} open={!!taskRowId} onClose={() => setTaskRowId(null)}>
    {selected && <div className="platform-stack"><Descriptions column={1} bordered items={[{key:'processing',label:'解析与索引',children:<ProcessingStatus value={selected.processing_status} />}, {key:'publication',label:'发布状态',children:<PublicationStatus value={selected.publication_status} />}, {key:'attempts',label:'尝试次数',children:selected.job?.attempts ?? '—'}, {key:'progress',label:'当前说明',children:selected.job?.error || selected.job?.progress?.message || '暂无独立任务记录'}]} />{selected.processing_status === 'partial' && <Alert type="warning" showIcon message="处理已结束，部分步骤未完成" description="无需等待或重新审核。先查看下方错误说明，修复对应配置或资料问题后可重新处理；已有发布决定会保留。" />}<Collapse defaultActiveKey={['jobs']} items={[{key:'jobs',label:`处理记录（${selected.jobs.length} 条）`,children:<Json value={selected.jobs} />}]} /></div>}
  </Drawer>{classificationAsset && <ClassificationEditor asset={classificationAsset} onClose={() => setClassificationAsset(null)} onSaved={async () => { await refreshAll(); if (detail?.asset?.id === classificationAsset.id) await open(classificationAsset.id); message.success('分类与知识树已更新'); }} />}<Drawer title={detail?.asset?.title || "资料详情"} width={820} open={!!detail} onClose={() => setDetail(null)}><div className="asset-detail"><ClassificationDetail value={detail?.asset?.classification} /><Descriptions column={1} bordered items={[
    { key: "id", label: "资产编号", children: detail?.asset?.id }, { key: "hash", label: "原件 SHA-256", children: detail?.asset?.sha256 },
    { key: "processing", label: "解析与索引", children: <ProcessingStatus value={detail?.asset?.processing_status} /> },
    { key: "publication", label: "发布状态", children: <PublicationStatus value={detail?.asset?.publication_status} /> },
    ...(detail?.asset?.pdf_extraction ? [{key:'pdf', label:'PDF 逐页解析', children:<><p>共 {detail.asset.pdf_extraction.page_count} 页，其中 {detail.asset.pdf_extraction.ocr_pages} 页使用本地 OCR，原始页码保留。</p>{detail.asset.pdf_extraction.ocr_pages > 0 && <p>OCR 文字属于机器识别结果；疑难字请对照原件，逐页方法与识别置信度可在下方处理记录中查看。</p>}{detail.asset.pdf_extraction.unresolved_pages?.length > 0 && <Alert type="warning" message={`未识别页：${detail.asset.pdf_extraction.unresolved_pages.join('、')}`} />}</>}] : []),
  ]} />{detail?.asset?.text_sanitization?.replaced_count > 0 && <Alert style={{ marginTop: 16 }} type="warning" showIcon message={`解析正文已修复 ${detail.asset.text_sanitization.replaced_count} 个非法 Unicode 字符`} description="原始文件保持不变；仅派生正文中的不可编码字符替换为 �，后续索引继续使用修复后的正文。" />}<MediaPreview asset={detail?.asset} />{detail?.asset?.model_extraction && <Alert style={{ marginTop: 16 }} type="info" message="以下内容由模型提取，属于待核对的观察结果，请与原件对照。" />}{detail?.asset?.wiki?.wiki?.summary && <><h3>知识摘要</h3><Alert type="info" message="自动生成的知识概括，请结合原文核对。"/><p>{detail.asset.wiki.wiki.summary}</p></>}{detail?.asset?.graph_candidates && <><h3>概念与原文锚点</h3><p>自动提取的候选概念，尚不代表事实关系确认。</p><Json value={detail.asset.graph_candidates}/></>}{detail?.asset?.extracted_text && <><h3>解析正文</h3><pre>{detail.asset.extracted_text}</pre></>}<Collapse items={[{ key: "trace", label: "证据、特征与处理记录", children: <Json value={detail} /> }]} /></div></Drawer>
    {review && <PublicationManager assetId={review.id} canManage={user?.role === 'admin'} onClose={() => setReview(null)} onSaved={async () => { await refreshAll(); if (detail?.asset?.id === review.id) await open(review.id); }} />}
  </>;
}

function MediaPreview({ asset }: { asset: any }) {
  if (!asset) return null;
  const url = API + `/assets/${asset.id}/preview`;
  if (["image/jpeg", "image/png", "image/webp", "image/gif"].includes(asset.mime)) return <img src={url} alt={asset.title} style={{ maxWidth: "100%", maxHeight: 460, objectFit: "contain", marginTop: 16 }} />;
  if (["video/mp4", "video/webm"].includes(asset.mime)) return <video src={url} controls preload="metadata" style={{ maxWidth: "100%", maxHeight: 460, marginTop: 16 }} />;
  if (["audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg"].includes(asset.mime)) return <audio src={url} controls preload="metadata" style={{ maxWidth: "100%", marginTop: 16 }} />;
  return null;
}
