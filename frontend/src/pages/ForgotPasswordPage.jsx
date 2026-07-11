import { useState } from "react";
import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import toast from "react-hot-toast";
import api from "../lib/api";
import { Input, Button } from "../components/ui";

export default function ForgotPasswordPage() {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const { register, handleSubmit, formState: { errors } } = useForm();

  const onSubmit = async (data) => {
    setLoading(true);
    try {
      await api.post("/auth/forgot-password", { email: data.email });
      setSent(true);
    } catch {
      toast.error("Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--paper)", padding: "2rem" }}>
      <div style={{ width: "100%", maxWidth: 400 }}>
        <div style={{ marginBottom: "2rem" }}>
          <Link to="/login" style={{ fontFamily: "var(--font-display)", fontSize: "1.4rem", fontWeight: 300, color: "var(--ink)", textDecoration: "none" }}>
            Text<span style={{ color: "var(--accent)" }}>Lens</span>
          </Link>
        </div>
        {sent ? (
          <div className="card" style={{ padding: "2rem", textAlign: "center" }}>
            <div style={{ fontSize: "2rem", marginBottom: "1rem", color: "var(--success)" }}>&#10003;</div>
            <h2 style={{ fontWeight: 400, marginBottom: "0.5rem" }}>Check your email</h2>
            <p style={{ color: "var(--ink-muted)", fontSize: "0.9rem", marginBottom: "1.5rem" }}>
              If that email is registered, you will receive a reset link shortly.
            </p>
            <Link to="/login" className="btn btn-outline" style={{ display: "inline-flex" }}>Back to sign in</Link>
          </div>
        ) : (
          <div className="card" style={{ padding: "2rem" }}>
            <h1 style={{ fontSize: "1.5rem", fontWeight: 300, marginBottom: "0.5rem" }}>Reset password</h1>
            <p style={{ color: "var(--ink-muted)", fontSize: "0.88rem", marginBottom: "1.5rem" }}>
              Enter your email and we will send you a reset link.
            </p>
            <form onSubmit={handleSubmit(onSubmit)} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              <Input label="Email address" type="email" placeholder="you@example.com" error={errors.email?.message}
                {...register("email", { required: "Email required" })} />
              <Button type="submit" loading={loading} style={{ width: "100%" }}>Send reset link</Button>
            </form>
            <div style={{ marginTop: "1rem", textAlign: "center" }}>
              <Link to="/login" style={{ fontSize: "0.85rem", color: "var(--ink-muted)" }}>Back to sign in</Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
