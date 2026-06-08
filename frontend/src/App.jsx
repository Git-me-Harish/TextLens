import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { AuthProvider } from "./lib/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./components/layout/AppLayout";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import AuthCallbackPage from "./pages/AuthCallbackPage";
import DashboardPage from "./pages/DashboardPage";
import HistoryPage from "./pages/HistoryPage";
import ProfilePage from "./pages/ProfilePage";
import PDFChatPage from "./pages/PDFChatPage";
import PipelinesPage from "./pages/PipelinesPage";
import AgentHistoryPage from "./pages/AgentHistoryPage";
import { ImageOCRPage, PDFExtractPage, SummarizePage, PDFToWordPage } from "./pages/ToolPages";

function PL({ children }) {
  return <ProtectedRoute><AppLayout>{children}</AppLayout></ProtectedRoute>;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster position="top-right" toastOptions={{ duration: 3500, style: { fontFamily: "var(--font-body)", fontSize: "0.875rem" } }} />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />

          <Route path="/dashboard" element={<PL><DashboardPage /></PL>} />
          <Route path="/history" element={<PL><HistoryPage /></PL>} />
          <Route path="/profile" element={<PL><ProfilePage /></PL>} />
          <Route path="/pipelines" element={<PL><PipelinesPage /></PL>} />
          <Route path="/agent-history" element={<PL><AgentHistoryPage /></PL>} />
          <Route path="/tools/ocr-image" element={<PL><ImageOCRPage /></PL>} />
          <Route path="/tools/pdf-extract" element={<PL><PDFExtractPage /></PL>} />
          <Route path="/tools/summarize" element={<PL><SummarizePage /></PL>} />
          <Route path="/tools/pdf-to-word" element={<PL><PDFToWordPage /></PL>} />
          <Route path="/tools/pdf-chat" element={<PL><PDFChatPage /></PL>} />

          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}