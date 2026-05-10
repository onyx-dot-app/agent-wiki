"use client";

import Link from "next/link";
import { Suspense, useState, type CSSProperties, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/common/Button";
import { useAuth } from "@/lib/auth";
import { color, radius, shadow } from "@/lib/theme";

const OIDC_ERROR_MESSAGES: Record<string, string> = {
  oidc_exchange_failed: "Couldn't complete sign-in. Try again.",
  oidc_userinfo_failed: "Couldn't fetch your profile from the identity provider.",
  oidc_no_email: "The identity provider didn't return an email address.",
  oidc_email_unverified: "Your email isn't verified with the identity provider.",
  oidc_email_not_allowed: "Your email isn't on the allow list for this workspace.",
};

// Inputs share the same chrome as the rest of the app — tokenized border,
// sm radius, 8/10 padding. Defined once so login + signup stay in sync.
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

function LoginForm() {
  const { login, config } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      const next = params.get("next") || "/";
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  const next = params.get("next");
  const signupHref = next ? `/signup?next=${encodeURIComponent(next)}` : "/signup";
  const oidcError = params.get("error");
  const oidcErrorMessage = oidcError ? OIDC_ERROR_MESSAGES[oidcError] ?? oidcError : null;
  const isOidc = config?.mode === "oidc";

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
          Sign in
        </h1>
        <p style={{ margin: 0, marginBottom: 24, fontSize: 14, color: color.text.muted }}>
          {isOidc ? "Continue with your workspace identity provider." : "Welcome back."}
        </p>

        {oidcErrorMessage && (
          <div
            style={{
              marginBottom: 16,
              padding: "10px 12px",
              fontSize: 13,
              background: color.state.danger.bg,
              border: `1px solid ${color.state.danger.border}`,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
            }}
          >
            {oidcErrorMessage}
          </div>
        )}

        {isOidc ? (
          // Native <a> so the browser performs a full navigation to the
          // OIDC start endpoint. Styled to match Button variant="primary"
          // size="md" — keep in sync with components/common/Button.tsx.
          <a
            href="/api/auth/oidc/login"
            style={{
              display: "block",
              width: "100%",
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: 600,
              lineHeight: 1.2,
              textAlign: "center",
              background: color.accent.bg,
              color: color.accent.fg,
              border: `1px solid ${color.accent.bg}`,
              borderRadius: radius.md,
              textDecoration: "none",
              boxSizing: "border-box",
            }}
          >
            Sign in with Google
          </a>
        ) : (
          <>
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
                <div style={{ marginBottom: 6 }}>Password</div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  style={inputStyle}
                />
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
              <Button
                type="submit"
                variant="primary"
                disabled={submitting}
                style={{ marginTop: 4, width: "100%" }}
              >
                {submitting ? "Signing in…" : "Sign in"}
              </Button>
            </form>
            <p style={{ marginTop: 20, marginBottom: 0, fontSize: 13, color: color.text.muted }}>
              {config?.signup_open === false ? (
                "Signup is restricted — contact an admin."
              ) : (
                <>
                  Don&apos;t have an account?{" "}
                  <Link href={signupHref} style={{ color: color.text.primary, textDecoration: "underline" }}>
                    Sign up
                  </Link>
                </>
              )}
            </p>
          </>
        )}
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main style={{ padding: 32 }}>Loading…</main>}>
      <LoginForm />
    </Suspense>
  );
}
