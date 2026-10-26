import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Drawer, Table, Tag } from 'antd';
import { listTasks } from '../../api/endpoints';
import { errorMessage } from '../../api/client';
import type { ImportTaskInfo } from '../../api/types';
import ImportProgress, { STATUS_COLOR } from '../../components/ImportProgress';
import { LoadError, Refresh } from './shared';

export function StructuredImportTasks() {
  const [items, setItems] = useState<ImportTaskInfo[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { setItems(await listTasks()); setError(''); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 4000); return () => clearInterval(timer); }, [refresh]);
  const detail = items.find(item => item.task_uuid === selected);
  const running = detail?.status === 'running' || detail?.status === 'pending';
  const percent = detail ? running ? detail.total > 0 ? Math.min(99, Math.round((detail.succeeded + detail.skipped + detail.failed) / detail.total * 100)) : 0 : 100 : 0;
  return <><LoadError error={error} /><Card title="结构化导入记录" extra={<Refresh onClick={refresh} />}>
    <Table<ImportTaskInfo> rowKey="task_uuid" loading={loading} dataSource={items} scroll={{x:1000}} pagination={{pageSize:10}} columns={[
      {title:'数据集',dataIndex:'dataset',width:160}, {title:'文件 / 来源',dataIndex:'file_name'},
      {title:'导入模式',dataIndex:'mode',render:v => ({dedupe:'去重导入',replace:'覆盖重导',append:'追加导入'}[v as string] || v)},
      {title:'状态',dataIndex:'status',render:v => <Tag color={STATUS_COLOR[v]}>{({success:'已完成',partial:'部分完成',failed:'失败',running:'处理中',pending:'排队中'} as Record<string,string>)[v] || v}</Tag>},
      {title:'已处理',dataIndex:'processed'}, {title:'成功',dataIndex:'succeeded'}, {title:'跳过',dataIndex:'skipped'}, {title:'失败',dataIndex:'failed'},
      {title:'操作',render:(_,row) => <Button size="small" onClick={() => setSelected(row.task_uuid)}>任务详情</Button>},
    ]} />
  </Card><Drawer title="结构化导入任务详情" width={760} open={!!selected} onClose={() => setSelected(null)}>{detail && <ImportProgress task={detail} percent={percent} />}</Drawer></>;
}
