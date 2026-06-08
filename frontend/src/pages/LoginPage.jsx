import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import toast from "react-hot-toast";
import { useAuth } from "../lib/AuthContext";
import { Input, Button } from "../components/ui";
import api from "../lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const { register, handleSubmit, formState: { errors } } = useForm();

  const onSubmit = async (data) => {
    setLoading(true);
    try {
      await login(data.email, data.password);
      navigate("/dashboard");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const googleLogin = async () => {
    try {
      const { data } = await api.get("/auth/google/login");
      window.location.href = data.url;
    } catch { toast.error("Google login unavailable"); }
  };

  return (
    <div className="auth-page">
      <div className="auth-brand">
        <div style={{ maxWidth: 420 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: "2.5rem", fontWeight: 300, marginBottom: "1.5rem", lineHeight: 1.2 }}>
            Text<span style={{ color: "#93c5fd" }}>Lens</span>
          </div>
          <p style={{ fontSize: "1.15rem", color: "rgba(255,255,255,0.7)", lineHeight: 1.7, marginBottom: "2.5rem" }}>
            Extract, analyze, and transform your documents with precision OCR technology.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {["Image OCR — extract text from any image", "PDF Intelligence — structure & summarize", "Document Q&A — ask questions, get answers", "Export to Word — one-click conversion"].map(f => (
              <div key={f} style={{ display: "flex", alignItems: "center", gap: 10, color: "rgba(255,255,255,0.65)", fontSize: "0.88rem" }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#93c5fd", flexShrink: 0 }} />
                {f}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="auth-form-side">
        <div style={{ marginBottom: "2rem" }}>
          <h1 style={{ fontSize: "1.75rem", fontWeight: 300, marginBottom: "0.5rem" }}>Welcome back</h1>
          <p style={{ color: "var(--ink-muted)", fontSize: "0.9rem" }}>Sign in to your TextLens account</p>
        </div>

        <button onClick={googleLogin} className="btn btn-outline" style={{ width: "100%", marginBottom: "1.5rem", gap: 10 }}>
          <svg width="18" height="18" viewBox="0 0 24 24"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
          Continue with Google
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "1.5rem" }}>
          <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
          <span style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>or</span>
          <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
        </div>

        <form onSubmit={handleSubmit(onSubmit)} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <Input label="Email address" type="email" placeholder="you@example.com" error={errors.email?.message}
            {...register("email", { required: "Email is required" })} />
          <Input label="Password" type="password" placeholder="••••••••" error={errors.password?.message}
            {...register("password", { required: "Password is required" })} />
          <div style={{ textAlign: "right", marginTop: -4 }}>
            <Link to="/forgot-password" style={{ fontSize: "0.82rem", color: "var(--accent)" }}>Forgot password?</Link>
          </div>
          <Button type="submit" loading={loading} style={{ width: "100%", marginTop: 4 }}>Sign in</Button>
        </form>

        <p style={{ marginTop: "1.5rem", textAlign: "center", fontSize: "0.88rem", color: "var(--ink-muted)" }}>
          No account? <Link to="/register">Create one</Link>
        </p>
      </div>
    </div>
  );
}
