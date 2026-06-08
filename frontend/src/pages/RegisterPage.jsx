import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import toast from "react-hot-toast";
import { useAuth } from "../lib/AuthContext";
import { Input, Button } from "../components/ui";

export default function RegisterPage() {
  const { register: authRegister } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const { register, handleSubmit, watch, formState: { errors } } = useForm();

  const onSubmit = async (data) => {
    setLoading(true);
    try {
      await authRegister(data.email, data.full_name, data.password);
      toast.success("Account created!");
      navigate("/dashboard");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-brand">
        <div style={{ maxWidth: 420 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: "2.5rem", fontWeight: 300, marginBottom: "1rem", lineHeight: 1.2 }}>
            Text<span style={{ color: "#93c5fd" }}>Lens</span>
          </div>
          <p style={{ fontSize: "1rem", color: "rgba(255,255,255,0.65)", lineHeight: 1.7 }}>
            Join thousands of professionals who use TextLens to unlock the data trapped in their documents.
          </p>
        </div>
      </div>

      <div className="auth-form-side">
        <div style={{ marginBottom: "2rem" }}>
          <h1 style={{ fontSize: "1.75rem", fontWeight: 300, marginBottom: "0.5rem" }}>Create account</h1>
          <p style={{ color: "var(--ink-muted)", fontSize: "0.9rem" }}>Start extracting text from documents for free</p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <Input label="Full name" placeholder="Jane Smith" error={errors.full_name?.message}
            {...register("full_name", { required: "Name is required" })} />
          <Input label="Email address" type="email" placeholder="jane@company.com" error={errors.email?.message}
            {...register("email", { required: "Email is required" })} />
          <Input label="Password" type="password" placeholder="At least 8 characters" error={errors.password?.message}
            {...register("password", { required: "Password is required", minLength: { value: 8, message: "Minimum 8 characters" } })} />
          <Input label="Confirm password" type="password" placeholder="Repeat password" error={errors.confirm?.message}
            {...register("confirm", { validate: v => v === watch("password") || "Passwords do not match" })} />
          <Button type="submit" loading={loading} style={{ width: "100%", marginTop: 4 }}>Create account</Button>
        </form>

        <p style={{ marginTop: "1.5rem", textAlign: "center", fontSize: "0.88rem", color: "var(--ink-muted)" }}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
