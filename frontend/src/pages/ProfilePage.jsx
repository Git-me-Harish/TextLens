import { useState } from "react";
import { useForm } from "react-hook-form";
import toast from "react-hot-toast";
import { useAuth } from "../lib/AuthContext";
import api from "../lib/api";
import { Input, Button } from "../components/ui";

export default function ProfilePage() {
  const { user, reload } = useAuth();
  const [loading, setLoading] = useState(false);
  const { register, handleSubmit } = useForm({ defaultValues: { full_name: user?.full_name || "" } });

  const onSubmit = async (data) => {
    setLoading(true);
    try {
      await api.patch("/users/me", data);
      await reload();
      toast.success("Profile updated");
    } catch { toast.error("Update failed"); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ maxWidth: 560 }}>
      <div className="page-header">
        <h1 className="page-title">Profile</h1>
        <p className="page-subtitle">Manage your account settings</p>
      </div>

      <div className="card" style={{ padding: "1.5rem", marginBottom: "1.5rem", display: "flex", alignItems: "center", gap: "1.25rem" }}>
        {user?.avatar_url ? (
          <img src={user.avatar_url} alt="" style={{ width: 56, height: 56, borderRadius: "50%", objectFit: "cover" }} />
        ) : (
          <div style={{ width: 56, height: 56, borderRadius: "50%", background: "var(--accent-light)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontSize: "1.25rem", color: "var(--accent)" }}>
            {user?.full_name?.[0] || "U"}
          </div>
        )}
        <div>
          <div style={{ fontWeight: 500 }}>{user?.full_name}</div>
          <div style={{ fontSize: "0.85rem", color: "var(--ink-muted)" }}>{user?.email}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", marginTop: 2, textTransform: "uppercase", letterSpacing: "0.05em" }}>{user?.role}</div>
        </div>
      </div>

      <div className="card" style={{ padding: "1.5rem" }}>
        <h2 style={{ fontSize: "1rem", fontWeight: 500, marginBottom: "1.25rem" }}>Edit details</h2>
        <form onSubmit={handleSubmit(onSubmit)} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <Input label="Full name" {...register("full_name")} />
          <Input label="Email" defaultValue={user?.email} disabled />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button type="submit" loading={loading}>Save changes</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
