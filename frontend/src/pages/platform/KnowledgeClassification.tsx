import { Alert, Button, Descriptions, Form, Input, Modal, Select, Tag } from 'antd';
import { useState } from 'react';
import { http, errorMessage } from '../../api/client';
import { API } from './shared';

const domains = { medical: '医疗', equipment: '设备', finance: '财务', legal: '法律', quality: '质量', safety: '安全', humanities: '人文典籍', general: '待归类' };
const statuses: Record<string, string> = { automatic: '自动分类', manual: '人工纠偏', needs_classification: '待归类' };

export function ClassificationTag({ value }: { value: any }) {
  if (!value) return <span>尚未分类</span>;
  return <Tag color={value.status === 'needs_classification' ? 'orange' : value.status === 'manual' ? 'purple' : 'blue'}>{value.label} · {statuses[value.status] || value.status}</Tag>;
}

export function ClassificationDetail({ value }: { value: any }) {
  if (!value) return null;
  return <Descriptions column={1} bordered size="small" style={{ marginTop: 16 }} items={[
    { key: 'subject', label: '文档主领域', children: <ClassificationTag value={value} /> },
    { key: 'basis', label: '自动分类依据', children: <>{(value.basis || []).map((line: string) => <p key={line}>{line}</p>)}<small>结合标题、章节与全文的本地主题规则；支持度不代表事实可信度。</small></> },
    { key: 'related', label: '其他领域线索', children: (value.related_domains || []).map((d: string) => domains[d as keyof typeof domains] || d).join('、') || '无' },
    ...(value.override ? [{ key: 'override', label: '纠偏记录', children: `${value.override.reason}（${value.override.actor}，${value.override.at}）` }] : []),
    { key: 'version', label: '分类规则版本', children: value.version },
  ]} />;
}

export function ClassificationEditor({ asset, onClose, onSaved }: { asset: any; onClose: () => void; onSaved: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <Modal title={`分类纠偏 · ${asset.title}`} open onCancel={busy ? undefined : onClose} closable={!busy} maskClosable={!busy} footer={null}>
    <ClassificationDetail value={asset.classification} />
    <Alert style={{ marginTop: 16 }} type="info" message="整份资料的主树归属会更新，知识单元与引用保持原文定位。原分类快照留档，发布复核单独保留。" />
    <Form layout="vertical" initialValues={{ domain: asset.classification_override?.domain || 'auto' }} onFinish={async values => {
      setBusy(true); setError('');
      try {
        await http.post(API + `/assets/${asset.id}/classification`, { domain: values.domain === 'auto' ? null : values.domain, reason: values.reason });
        await onSaved(); onClose();
      } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
    }}>
      <Form.Item name="domain" label="主领域" rules={[{ required: true }]}><Select disabled={busy} options={[{ value: 'auto', label: '自动识别（移除人工覆盖）' }, ...Object.entries(domains).map(([value, label]) => ({ value, label }))]} /></Form.Item>
      <Form.Item name="reason" label="纠偏依据" rules={[{ required: true, whitespace: true, min: 2, max: 500 }]}><Input.TextArea rows={3} disabled={busy} /></Form.Item>
      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}
      <Button type="primary" htmlType="submit" loading={busy}>保存并更新知识树</Button>
      {busy && <p>正在复用已有解析和向量重建分类，请等待完成。</p>}
    </Form>
  </Modal>;
}
