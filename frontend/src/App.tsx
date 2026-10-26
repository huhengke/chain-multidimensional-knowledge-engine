import { Spin } from "antd";
import {
  Navigate,
  Route,
  BrowserRouter,
  Routes,
  useLocation,
} from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import AppLayout from "./layout/AppLayout";
import ChatPage from "./pages/Chat";
import Datasets from "./pages/Datasets";
import DatasetWizard from "./pages/DatasetWizard";
import ReinitializePage from "./pages/Reinitialize";
import SystemParamsPage from "./pages/SystemParams";
import Login from "./pages/Login";
import ModelConfigPage from "./pages/ModelConfig";
import FoundationFeaturesPage from "./pages/FoundationFeatures";
import TopologyPage from "./pages/Topology";
import RuntimeProfilesPage from "./pages/RuntimeProfiles";
import { IntakeCenter, JobCenter } from "./pages/platform/DataCenter";
import { Overview, ModelCenter, SettingsCenter, AuditCenter, StreamCenter, TopologyCenter } from "./pages/platform/Operations";
import { SearchWorkspace } from "./pages/platform/Workspaces";
import KnowledgeAgent from "./pages/platform/KnowledgeAgent";
import MemoryAgent from "./pages/platform/MemoryAgent";
import UnifiedAgent from "./pages/platform/UnifiedAgent";

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div style={{ textAlign: "center", marginTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!user) {
    return (
      <Navigate to="/login" state={{ from: location.pathname }} replace />
    );
  }
  return children;
}

function DataCenterRedirect({ to, brain, tab }: { to: string; brain?: string; tab?: string }) {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  if (brain) params.set('brain', brain);
  if (tab) params.set('tab', tab);
  return <Navigate to={{pathname:to, search:params.toString() ? `?${params}` : ''}} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <Protected>
              <AppLayout />
            </Protected>
          }
        >
          <Route path="/agent" element={<UnifiedAgent />} />
          <Route path="/structured/agent" element={<ChatPage />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/sources" element={<DataCenterRedirect to="/intake" />} />
          <Route path="/intake" element={<IntakeCenter />} />
          <Route path="/jobs" element={<JobCenter />} />
          <Route path="/assets" element={<DataCenterRedirect to="/jobs" />} />
          <Route path="/models" element={<ModelCenter />} />
          <Route path="/settings" element={<SettingsCenter />} />
          <Route path="/audit" element={<AuditCenter />} />
          <Route path="/streams" element={<StreamCenter />} />
          <Route path="/cognition" element={<TopologyCenter />} />
          <Route path="/knowledge/agent" element={<KnowledgeAgent />} />
          <Route path="/memory/agent" element={<MemoryAgent />} />
          <Route path="/knowledge/search" element={<SearchWorkspace key="knowledge" brain="knowledge" />} />
          <Route path="/memory/search" element={<SearchWorkspace key="memory" brain="memory" />} />
          <Route path="/agent/topology" element={<TopologyPage />} />
          <Route path="/chat" element={<Navigate to="/agent" replace />} />
          <Route path="/records" element={<DataCenterRedirect to="/datasets" />} />
          <Route path="/import" element={<DataCenterRedirect to="/datasets" tab="import" />} />
          <Route path="/rules" element={<Navigate to="/foundation-features?tab=time-rules" replace />} />
          <Route path="/system-params" element={<SystemParamsPage />} />
          <Route path="/runtime-profiles" element={<RuntimeProfilesPage />} />
          <Route path="/datasources" element={<DataCenterRedirect to="/datasets" tab="sources" />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="/foundation-features" element={<FoundationFeaturesPage />} />
          <Route path="/datasets/new" element={<DatasetWizard />} />
          <Route path="/reinitialize" element={<ReinitializePage />} />
          <Route path="/model-config" element={<ModelConfigPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

