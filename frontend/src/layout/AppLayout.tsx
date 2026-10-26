import { ApartmentOutlined, DatabaseOutlined, MessageOutlined, SettingOutlined, AppstoreOutlined, RadarChartOutlined, LogoutOutlined, MenuOutlined } from "@ant-design/icons";
import { useState } from "react";
import { Button, Layout, Menu, Tag, Typography, message } from "antd";
import { errorMessage } from "../api/client";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import "../platform.css";
const { Sider, Header, Content } = Layout;
export default function AppLayout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  const selectedKey = location.pathname === "/datasets" || location.pathname.startsWith("/datasets/")
    ? "/intake"
    : location.pathname;
  const items = [
    { key: "/overview", label: "运行总览", icon: <AppstoreOutlined /> },
    { key: "/agent", label: "统一 Agent 工作台", icon: <MessageOutlined /> },
      { key: "data", label: "数据中心", icon: <DatabaseOutlined />, children: [
      { key: "/intake", label: "数据接入" }, { key: "/jobs", label: "导入任务" },
    ] },
    { key: "cognition", label: "认知与检索", icon: <RadarChartOutlined />, children: [
      { key: "/knowledge/search", label: "知识证据检索" }, { key: "/memory/search", label: "时空记录检索" },
      { key: "/streams", label: "实时采集" }, { key: "/cognition", label: "智慧树与星团" },
    ] },
    { key: "governance", label: "配置与治理", icon: <SettingOutlined />, children: [
      { key: "/models", label: "模型配置" }, { key: "/foundation-features", label: "基座特征管理" },
      { key: "/runtime-profiles", label: "AI 引擎与数据集" }, { key: "/settings", label: "系统设置" }, { key: "/audit", label: "操作审计" },
    ] },
  ];
  return <Layout className="platform-shell">
    {mobile && !collapsed && <button className="mobile-menu-shade" aria-label="关闭导航" onClick={() => setCollapsed(true)} />}
    <Sider className="platform-sidebar" width={232} breakpoint="lg" collapsedWidth={0} theme="light" trigger={null} collapsed={collapsed} onBreakpoint={broken => { setMobile(broken); setCollapsed(broken); }}>
      <div className="platform-brand"><ApartmentOutlined /><div><strong>链式多维时空知识库引擎</strong><small>CHAINED KNOWLEDGE ENGINE</small></div></div>
      <Menu mode="inline" items={items} selectedKeys={[selectedKey]} defaultOpenKeys={["data", "cognition", "governance"]} onClick={({ key }) => { navigate(key); if (mobile) setCollapsed(true); }} />
    </Sider>
    <Layout className="platform-main">
      <Header className="platform-header">{mobile && <Button aria-label="展开导航" icon={<MenuOutlined />} onClick={() => setCollapsed(false)} />}<Typography.Text type="secondary">认知与知识引擎 / 管理工作区</Typography.Text><div><Tag>{user?.role === "admin" ? "管理员" : user?.role === "operator" ? "运营" : "查看者"}</Tag><span>{user?.display_name || user?.username}</span><Button type="text" icon={<LogoutOutlined />} onClick={async () => { try { await signOut(); navigate("/login"); } catch (e) { message.error(errorMessage(e)); } }}>退出</Button></div></Header>
      <Content className="platform-content"><Outlet /></Content>
    </Layout>
  </Layout>;
}

