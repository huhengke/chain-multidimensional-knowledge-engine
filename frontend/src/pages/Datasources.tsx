import { ApiOutlined, DatabaseOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tag,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { http, errorMessage } from "../api/client";
import { useAuth } from '../auth/AuthContext';
import { listDatasources, listDatasourceTables, testDatasource } from "../api/endpoints";

const TYPE_OPTIONS = [
  { value: "mysql", label: "MySQL" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "oracle", label: "Oracle" },
  { value: "mongo", label: "MongoDB" },
];

export default function DatasourcesPage() {
  const {user}=useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [selectedSource, setSelectedSource] = useState("");
  const [tables, setTables] = useState<{ name: string; schema?: string }[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(
    null
  );
  const [form] = Form.useForm();
  const save=async()=>{try{const v=await form.validateFields();await http.put('/api/v1/engine/datasources',v);await load();message.success('数据源已加密保存');}catch(e){message.error(errorMessage(e));}};

  const load = async () => {
    try {
      const r = await listDatasources();
      setItems(r.items);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onTest = async () => {
    const v = await form.validateFields();
    setTesting(true);
    setResult(null);
    try {
      const r = await testDatasource(v);
      setResult({ ok: !!r.ok, message: r.message || (r.ok ? "连接成功" : "连接失败") });
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e) });
    } finally {
      setTesting(false);
    }
  };

  const discoverTables = async (sourceId: string) => {
    setSelectedSource(sourceId);
    setLoadingTables(true);
    try {
      const r = await listDatasourceTables(sourceId);
      setTables(r.items as { name: string; schema?: string }[]);
    } catch (e) {
      setTables([]);
      message.error(errorMessage(e));
    } finally {
      setLoadingTables(false);
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name", width: 140 },
    {
      title: "类型",
      dataIndex: "type",
      width: 120,
      render: (t: string) => <Tag color="blue">{t}</Tag>,
    },
    { title: "主机", dataIndex: "host" },
    { title: "端口", dataIndex: "port", width: 90 },
    { title: "库", dataIndex: "database" },
    { title: "用户", dataIndex: "username", width: 120 },
    {
      title: "操作",
      width: 210,
      render: (_: unknown, row: Record<string, unknown>) => (
        <Space><Button disabled={user?.role !== 'admin'} size="small" onClick={() => { form.resetFields(); form.setFieldsValue({...row,password:''}); }}>编辑</Button><Button
          size="small"
          icon={<DatabaseOutlined />}
          onClick={() => void discoverTables(String(row.id || row.name || ""))}
        >
          读取表清单
        </Button></Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="数据库页面负责连接配置、表发现和注册入口；定时拉取尚未接入 DataMapper、事实表和螺旋节点，仍属于规划能力。"
        description="选择数据源读取表清单后，可将选中的数据库表送入数据集管理，执行字段画像、AI 建议、人工确认和注册；注册完成后再从数据集管理执行导入。"
      />

      <Card title="已配置数据源">
        <Table
          size="small"
          rowKey={(r) => String(r.name || r.host)}
          columns={columns}
          dataSource={items}
          pagination={false}
        />
      </Card>

      {selectedSource && (
        <Card title={`表清单：${selectedSource}`} loading={loadingTables}>
          <Table
            size="small"
            rowKey={(row) => `${row.schema || ""}.${row.name}`}
            dataSource={tables}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: "表/集合", dataIndex: "name" },
              { title: "Schema", dataIndex: "schema" },
              {
                title: "操作",
                width: 150,
                render: (_: unknown, row: { name: string; schema?: string }) => {
                  const table = row.schema ? `${row.schema}.${row.name}` : row.name;
                  return (
                    <Button
                      type="primary"
                      size="small"
                      onClick={() => navigate(`/datasets/new?source=database&datasource=${encodeURIComponent(selectedSource)}&table=${encodeURIComponent(table)}`)}
                    >
                      一键注册
                    </Button>
                  );
                },
              },
            ]}
          />
        </Card>
      )}

      <Card title="登记数据源与连通性测试">
        <Row gutter={16}>
          <Col span={14}>
            <Form form={form} layout="vertical" initialValues={{ type: "mysql", port: 3306 }}>
              <Form.Item name="id" label="来源标识" rules={[{required:true},{pattern:/^[a-z][a-z0-9_]{0,39}$/,message:'使用小写英文开头，后接英文、数字或下划线'}]}><Input placeholder="例如 reporting_db"/></Form.Item>
              <Form.Item name="name" label="来源名称" rules={[{required:true}]}><Input/></Form.Item>
              <Button style={{marginBottom:16}} type="primary" disabled={user?.role!=='admin'} onClick={()=>void save()}>保存数据源</Button>
              <Row gutter={12}>
                <Col span={10}>
                  <Form.Item name="type" label="类型">
                    <Select options={TYPE_OPTIONS} />
                  </Form.Item>
                </Col>
                <Col span={14}>
                  <Form.Item
                    name="host"
                    label="主机"
                    rules={[{ required: true, message: "请输入主机" }]}
                  >
                    <Input placeholder="127.0.0.1" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="port" label="端口">
                    <InputNumber style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item name="database" label="数据库">
                    <Input />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="username" label="用户名">
                    <Input />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="password" label="密码">
                    <Input.Password />
                  </Form.Item>
                </Col>
              </Row>
              <Button
                type="primary"
                icon={<ApiOutlined />}
                loading={testing}
                onClick={onTest}
              >
                测试连接
              </Button>
            </Form>
          </Col>
          <Col span={10}>
            {result && (
              <Alert
                type={result.ok ? "success" : "error"}
                showIcon
                message={`${result.ok ? "连通成功" : "连通失败"}：${result.message}`}
              />
            )}
          </Col>
        </Row>
      </Card>
    </Space>
  );
}

