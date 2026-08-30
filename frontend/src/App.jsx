import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { AuthProvider } from "./lib/AuthContext";
import { AgentProvider } from "./lib/AgentContext";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./components/layout/AppLayout";
import useSSE from "./hooks/useSSE";

// Auth
import LoginPage          from "./pages/LoginPage";
import RegisterPage       from "./pages/RegisterPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import AuthCallbackPage   from "./pages/AuthCallbackPage";

// Core pages
import DashboardPage      from "./pages/DashboardPage";
import HistoryPage        from "./pages/HistoryPage";
import ProfilePage        from "./pages/ProfilePage";
import PipelinesPage      from "./pages/PipelinesPage";
import AgentHistoryPage   from "./pages/AgentHistoryPage";
import ActionHistoryPage  from "./pages/ActionHistoryPage";
import ApiKeysPage        from "./pages/ApiKeysPage";
import CredentialsSettingsPage from "./pages/CredentialsSettingsPage";
import BatchPage          from "./pages/BatchPage";
import SchedulesPage      from "./pages/SchedulesPage";

// Tools — new consolidated pages
import UniversalExtractorPage from "./pages/UniversalExtractorPage";
import DocumentStudioPage     from "./pages/DocumentStudioPage";
import SummarizePage          from "./pages/SummarizePage";
import PDFChatPage            from "./pages/PDFChatPage";
import ChatHistoryPage        from "./pages/ChatHistoryPage";

// Legacy shim redirects
import {
  ImageOCRPage,
  PDFExtractPage,
  PDFToWordPage,
} from "./pages/ToolPages";

function PL({ children }) {
  return <ProtectedRoute><AppLayout>{children}</AppLayout></ProtectedRoute>;
}

function SSEProvider({ children }) {
  useSSE();
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <AgentProvider>
        <BrowserRouter>
          <SSEProvider>
            <Toaster
              position="top-right"
              toastOptions={{
                duration: 3500,
                style: { fontFamily: "var(--font-body)", fontSize: "0.875rem" },
              }}
            />
            <Routes>
              <Route path="/login"           element={<LoginPage />} />
              <Route path="/register"        element={<RegisterPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
              <Route path="/auth/callback"   element={<AuthCallbackPage />} />

              <Route path="/dashboard"       element={<PL><DashboardPage /></PL>} />
              <Route path="/history"         element={<PL><HistoryPage /></PL>} />
              <Route path="/profile"         element={<PL><ProfilePage /></PL>} />
              <Route path="/pipelines"       element={<PL><PipelinesPage /></PL>} />
              <Route path="/agent-history"   element={<PL><AgentHistoryPage /></PL>} />
              <Route path="/actions/history" element={<PL><ActionHistoryPage /></PL>} />
              <Route path="/api-keys"        element={<PL><ApiKeysPage /></PL>} />
              <Route path="/settings/integrations" element={<PL><CredentialsSettingsPage /></PL>} />
              <Route path="/webhooks"        element={<PL><ApiKeysPage /></PL>} />
              <Route path="/batch"           element={<PL><BatchPage /></PL>} />
              <Route path="/schedules"       element={<PL><SchedulesPage /></PL>} />

              <Route path="/tools/extract"   element={<PL><UniversalExtractorPage /></PL>} />
              <Route path="/tools/studio"    element={<PL><DocumentStudioPage /></PL>} />
              <Route path="/tools/summarize" element={<PL><SummarizePage /></PL>} />
              <Route path="/tools/pdf-chat"  element={<PL><PDFChatPage /></PL>} />
              <Route path="/chat-history"    element={<PL><ChatHistoryPage /></PL>} />

              <Route path="/tools/ocr-image"   element={<ImageOCRPage />} />
              <Route path="/tools/pdf-extract" element={<PDFExtractPage />} />
              <Route path="/tools/pdf-to-word" element={<PDFToWordPage />} />

              <Route path="/"  element={<Navigate to="/dashboard" replace />} />
              <Route path="*"  element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </SSEProvider>
        </BrowserRouter>
      </AgentProvider>
    </AuthProvider>
  );
}
