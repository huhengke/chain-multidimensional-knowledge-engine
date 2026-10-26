import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Card, Form, Input, Typography } from "antd";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { http, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const { Title, Text } = Typography;

export default function Login() {
  const { message } = AntApp.useApp();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loading, setLoading] = useState(false);
  const [setup, setSetup] = useState(false);
  useEffect(() => { http.get('/api/v1/auth/status').then(r=>setSetup(r.data.setup_required)).catch(e=>message.error(errorMessage(e))); }, [message]);

  const from = (location.state as { from?: string })?.from || "/overview";

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      if (setup) await http.post('/api/v1/auth/setup', values);
      await signIn(values.username, values.password);
      message.success("登录成功");
      navigate(from, { replace: true });
    } catch (e) {
      message.error(errorMessage(e, "登录失败"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg,#0b1f3a,#123c6c 60%,#1b5f94)",
      }}
    >
      <Card style={{ width: 380 }} variant="borderless">
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Title level={3} style={{ marginBottom: 4 }}>
            链式多维时空知识库引擎
          </Title>
          <Text type="secondary">知识数据推理 · 时空记忆推理 · 结构化数据推理</Text>
        </div>
        <Form layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }, ...(setup ? [{min:12,message:'初始密码至少 12 位'}] : [])]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              size="large"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={loading}
            >
              {setup ? '创建本项目管理员并登录' : '登录'}
            </Button>
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {setup ? '首次使用请设置独立管理员账号。密码至少 12 位，不使用旧项目账号或默认密码。' : '使用本项目账号登录，数据与操作按账号权限开放。'}
        </Text>
      </Card>
    </div>
  );
}

