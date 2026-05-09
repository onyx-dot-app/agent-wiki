"use client";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { useRequireAuth } from "@/lib/auth";

export default function AdminPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <AppShell>
      <main style={{ padding: 32, maxWidth: 960 }}>
        <h1 style={{ marginTop: 0 }}>Admin</h1>
        <p style={{ color: "#666", marginTop: 0 }}>
          Manage who can sign in and which LLM the workspace uses.
        </p>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 16,
            marginTop: 24,
          }}
        >
          <AdminCard
            href="/admin/users"
            title="Users"
            description="View accounts, promote or demote admins, and remove users."
            icon={<UsersIcon />}
          />
          <AdminCard
            href="/admin/llm"
            title="LLM configuration"
            description="Choose an LLM provider to help maintain updates to the agent wiki and evaluate triggers"
            icon={<KeyIcon />}
          />
          <AdminCard
            href="/admin/web"
            title="Web search"
            description="Configure web search to help with drafting documents"
            icon={<GlobeIcon />}
          />
          <AdminCard
            href="/admin/groups"
            title="Groups"
            description="Create user groups to share wiki pages with."
            icon={<UsersIcon />}
          />
          <AdminCard
            href="/admin/health"
            title="Health"
            description="Backend liveness and background queue depth."
            icon={<HealthIcon />}
          />
        </div>
      </main>
    </AppShell>
  );
}

function AdminCard({
  href,
  title,
  description,
  icon,
}: {
  href: string;
  title: string;
  description: string;
  icon: ReactNode;
}) {
  return (
    <Link
      href={href}
      style={{
        display: "block",
        padding: 20,
        border: "1px solid #e5e5e5",
        borderRadius: 8,
        textDecoration: "none",
        color: "inherit",
        background: "white",
        transition: "border-color 0.15s, box-shadow 0.15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "#a5b4fc";
        e.currentTarget.style.boxShadow = "0 4px 16px rgba(79, 70, 229, 0.08)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "#e5e5e5";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ color: "#4f46e5" }}>{icon}</span>
        <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
      </div>
      <p style={{ margin: 0, color: "#666", fontSize: 14 }}>{description}</p>
    </Link>
  );
}

function UsersIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function KeyIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="8" cy="15" r="4" />
      <path d="M10.85 12.15 19 4" />
      <path d="M18 5l3 3" />
      <path d="M15 8l3 3" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

function HealthIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 12h4l2-6 4 12 2-6h6" />
    </svg>
  );
}
