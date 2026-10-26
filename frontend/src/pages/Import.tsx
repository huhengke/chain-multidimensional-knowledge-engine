import { Alert, Button, Card, Col, Radio, Row, Select, Space, Tag, message } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  importDataset,
  listDatasets,
  startTestImport,
} from "../api/endpoints";
import type { DatasetSummary } from "../api/types";
import ImportProgress from "../components/ImportProgress";
import { useImportTask } from "../hooks/useImportTask";

export default function ImportPage({ initialDataset }: { initialDataset?: string }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  // 新架构默认使用已注册的通用事实数据集。
  const [dataset, setDataset] = useState("oil_monthly_sales");
  const [mode, setMode] = useState("dedupe");
  const [file, setFile] = useState<File | null>(null);
  const { task, percent, running, watch } = useImportTask();

  useEffect(() => {
    void (async () => {
      try {
        const r = await listDatasets();
        const items = r.items;
        setDatasets(items);
        setDataset((current) =>
          initialDataset && items.some((item) => item.id === initialDataset)
            ? initialDataset
            : items.some((item) => item.id === current)
              ? current
              : items[0]?.id ?? "oil_monthly_sales"
        );
      } catch {
        /* 数据集列表失败时不允许回退到未注册的数据集导入 */
      }
    })();
  }, [initialDataset]);

  const current = datasets.find((d) => d.id === dataset);
  const baseData = Boolean(
    current && (current.source_origin || current.origin) === "base"
  );
  const databaseSource = current?.source?.kind === "database";
  const catalogSource = current?.source?.kind === "cnts" || current?.source_origin === "catalog" || current?.origin === "catalog";

  const start = async () => {
    if (catalogSource) return;
    try {
      const { task_uuid } = baseData
        ? await startTestImport(dataset, mode)
        : await importDataset(dataset, mode, databaseSource ? {} : { file: file || undefined });
      message.success("导入任务已触发");
      await watch(task_uuid);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card title="触发数据导入">
        {catalogSource && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="当前数据集属于固定版本的 CNTS Catalog，不能原地追加或覆盖。" description={<span>需离线构建并验收新的候选 Catalog，再由管理员在<Link to="/runtime-profiles">运行配置档</Link>中校验、激活。这里不会自动切换现有引擎。</span>} />}
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="这里只能导入已经在本工作区完成注册的数据集。"
          description="Excel 或数据库表必须先完成字段分析、角色确认和注册；数据库来源会直接从注册的数据源表读取，不能在这里临时指定未注册的表。"
        />
        <Row gutter={24} align="middle">
          <Col>
            <span style={{ marginRight: 8 }}>数据集：</span>
            <Select
              style={{ width: 200 }}
              value={dataset}
              onChange={setDataset}
              options={datasets.map((d) => ({
                value: d.id,
                label: `${d.label}（${d.id}）`,
              }))}
            />
          </Col>
          <Col>
            <span style={{ marginRight: 8 }}>模式：</span>
            <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
              <Radio.Button value="dedupe">去重(幂等)</Radio.Button>
              <Radio.Button value="replace">覆盖重导</Radio.Button>
              <Radio.Button value="append">直接追加</Radio.Button>
            </Radio.Group>
          </Col>
          <Col>
            {baseData ? (
              <Tag color="gold">基座数据：项目根目录来源（只读引用）</Tag>
            ) : databaseSource ? (
              <Tag color="cyan">数据库注册来源：{String(current?.source?.table || "已登记表")}</Tag>
            ) : (
              <input
                type="file"
                accept=".xlsx"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            )}
          </Col>
          <Col>
            <Button
              type="primary"
              onClick={start}
              loading={running}
              disabled={catalogSource || !current || (!baseData && !databaseSource && !file)}
            >
              开始导入
            </Button>
          </Col>
        </Row>
      </Card>

      {task && <ImportProgress task={task} percent={percent} />}

      <Link to="/jobs?brain=structured">查看结构化导入任务与历史记录 →</Link>
    </Space>
  );
}

