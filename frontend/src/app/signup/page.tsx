"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useAuth } from "@/lib/auth";
import { color, radius, shadow } from "@/lib/theme";

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  fontSize: 14,
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  background: color.bg.page,
  color: color.text.primary,
  boxSizing: "border-box",
  outline: "none",
};

function SignupForm() {
  const { signup, config } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  // OIDC mode has no password; bounce to /login where the SSO button lives.
  useEffect(() => {
    if (config?.mode === "oidc") {
      const next = params.get("next");
      router.replace(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
    }
  }, [config?.mode, params, router]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup(email, password, name || undefined);
      const next = params.get("next") || "/";
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "signup failed");
    } finally {
      setSubmitting(false);
    }
  }

  const next = params.get("next");
  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: color.bg.sunken,
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 400,
          background: color.bg.page,
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.lg,
          padding: 32,
          boxShadow: shadow.md,
        }}
      >
        <h1 style={{ margin: 0, marginBottom: 6, fontSize: 22, color: color.text.primary }}>
          Create account
        </h1>
        <p style={{ margin: 0, marginBottom: 24, fontSize: 14, color: color.text.muted }}>
          Set up your workspace login.
        </p>
        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <label style={{ fontSize: 13, color: color.text.secondary }}>
            <div style={{ marginBottom: 6 }}>Email</div>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              style={inputStyle}
            />
          </label>
          <label style={{ fontSize: 13, color: color.text.secondary }}>
            <div style={{ marginBottom: 6 }}>Name (optional)</div>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={inputStyle}
            />
          </label>
          <label style={{ fontSize: 13, color: color.text.secondary }}>
            <div style={{ marginBottom: 6 }}>Password</div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              style={inputStyle}
            />
            <div style={{ fontSize: 12, color: color.text.muted, marginTop: 6 }}>
              At least 8 characters.
            </div>
          </label>
          {error && (
            <div
              style={{
                padding: "10px 12px",
                fontSize: 13,
                background: color.state.danger.bg,
                border: `1px solid ${color.state.danger.border}`,
                color: color.state.danger.fg,
                borderRadius: radius.sm,
              }}
            >
              {error}
            </div>
          )}
          <div style={{ marginTop: 4 }}>
            <Button
              type="submit"
              variant="action"
              width="full"
              disabled={submitting}
            >
              {submitting ? "Creating…" : "Create account"}
            </Button>
          </div>
        </form>
        <p style={{ marginTop: 20, marginBottom: 0, fontSize: 13, color: color.text.muted }}>
          Already have an account?{" "}
          <Link href={loginHref} style={{ color: color.text.primary, textDecoration: "underline" }}>
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function SignupPage() {
  return (
    <Suspense
      fallback={
        <main style={{ padding: 32 }}>
          <LoadingSpinner center />
        </main>
      }
    >
      <SignupForm />
    </Suspense>
  );
}
