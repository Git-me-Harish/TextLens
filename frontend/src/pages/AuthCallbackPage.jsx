import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import { Spinner } from "../components/ui";

export default function AuthCallbackPage() {
  const [params] = useSearchParams();
  const { reload } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    const access = params.get("access_token");
    const refresh = params.get("refresh_token");
    if (access && refresh) {
      localStorage.setItem("access_token", access);
      localStorage.setItem("refresh_token", refresh);
      reload().then(() => navigate("/dashboard"));
    } else {
      navigate("/login");
    }
  }, []);

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center" }}>
        <Spinner size={32} />
        <p style={{ marginTop: 12, color: "var(--ink-muted)", fontSize: "0.9rem" }}>Signing you in...</p>
      </div>
    </div>
  );
}
