import { Alert, Button, Descriptions, Empty, Form, Input, Modal, Select, Spin, Tag, Timeline } from 'antd';
import { useEffect, useState } from 'react';
import { http, errorMessage } from '../../api/client';
import { API } from './shared';

const processingNames: Record<string, string> = { completed: '已完成', partial: '部分完成', queued: '排队中', running: '处理中', failed: '失败', cancelled: '已取消', unknown: '待确认' };
const publicationNames: Record<string, string> = { published: '已发布', pending_review: '待复核', rejected: '已拒绝', not_ready: '尚未进入发布', not_applicable: '不适用', unknown: '待确认' };
export const publicationActionName = (state: string) => ({ pending_review: '去复核', published: '发布管理', rejected: '重新复核' }[state] || '查看发布状态');
export function ProcessingStatus({ value }: { value: string }) {
  return <Tag color={value === 'completed' ? 'green' : value === 'failed' ? 'red' : value === 'partial' ? 'orange' : 'blue'}>{processingNames[value] || '待确认'}</Tag>;
}
export function PublicationStatus({ value }: { value: string }) {
  return <Tag color={value === 'published' ? 'green' : value === 'pending_review' ? 'orange' : value === 'rejected' ? 'red' : 'default'}>{publicationNames[value] || '待确认'}</Tag>;
}

export function PublicationManager({ assetId, canManage, onClose, onSaved }: { assetId: string; canManage: boolean; onClose: () => void; onSaved: () => Promise<void> }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [form] = Form.useForm();
  const load = async () => {
    setLoading(true);
    try { setData((await http.get(API + `/assets/${assetId}/publication`)).data); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [assetId]);
  const state = data?.asset?.publication_status;
  const options = state === 'pending_review' ? [{ value: 'approve', label: '批准发布' }, { value: 'reject', label: '拒绝发布' }]
    : state === 'published' ? [{ value: 'rollback', label: '撤回发布，转为待复核' }]
    : state === 'rejected' ? [{ value: 'rollback', label: '重新进入复核' }] : [];
  const actionable = canManage && !['queued', 'running', 'failed', 'cancelled'].includes(data?.asset?.processing_status) && options.length > 0;
  const act = async (values: any) => {
    setBusy(true); setError(''); setNotice('');
    try {
      await http.post(API + `/assets/${assetId}/review`, { ...values, expected_status: state });
      form.resetFields(); await load(); await onSaved(); setNotice('发布状态与审核记录已更新');
    } catch (e) { setError(errorMessage(e)); form.resetFields(); await load(); }
    finally { setBusy(false); }
  };
  return <Modal title={`${canManage ? publicationActionName(state) : '发布记录'}${data ? ` · ${data.asset.title}` : ''}`} open onCancel={busy ? undefined : onClose} closable={!busy} maskClosable={!busy} footer={null} width={680}>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    {notice && <Alert type="success" showIcon message={notice} style={{ marginBottom: 16 }} />}
    {loading ? <Spin /> : data && <>
      <Descriptions column={1} bordered size="small" items={[
        { key: 'processing', label: '解析与索引', children: <ProcessingStatus value={data.asset.processing_status} /> },
        { key: 'publication', label: '发布状态', children: <PublicationStatus value={state} /> },
      ]} />
      <p>{state === 'published' ? '资料已发布，可在授权范围内用于检索。撤回后停止作为已发布证据使用，原件与索引保留。' : '资料未发布，当前不能作为已发布证据用于回答。解析与索引结果仍保留。'}</p>
      {data.asset.processing_status === 'partial' && <Alert type="warning" message="资料处理仍有未完成项，批准发布不会补齐 OCR 缺页或摘要、概念处理结果。请在资料详情中核对。" />}
      {state === 'pending_review' && data.asset.review_reasons?.length > 0 && <Alert type="warning" message="待复核原因" description={data.asset.review_reasons.map((s: string) => <div key={s}>{s}</div>)} />}
      <h4>审核记录</h4>
      {data.history.length ? <Timeline items={data.history.map((r: any) => ({ children: <><strong>{r.action === 'approve' ? '批准发布' : r.action === 'reject' ? '拒绝发布' : '转为待复核'}</strong><div>{r.actor} · {new Date(r.created_at).toLocaleString('zh-CN', { hour12: false })}</div><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{r.reason}</p></> }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={state === 'published' ? '按规则自动发布，暂无人工审核记录' : '暂无人工审核记录'} />}
      {actionable && <Form form={form} layout="vertical" onFinish={act} disabled={busy}>
        <Form.Item name="action" label="发布操作" rules={[{ required: true, message: '请选择发布操作' }]}><Select options={options} /></Form.Item>
        <Form.Item name="reason" label="审核依据" rules={[{ required: true, whitespace: true, min: 2, max: 2000, message: '请填写 2 至 2000 字的审核依据' }]}><Input.TextArea rows={3} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>确认并记录</Button>
      </Form>}
    </>}
  </Modal>;
}
