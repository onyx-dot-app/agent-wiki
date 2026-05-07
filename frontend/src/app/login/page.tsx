"use client";

import Link from "next/link";
import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/lib/auth";

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

  return (
    <main style={{ maxWidth: 360, margin: "10vh auto", padding: 24 }}>
      <h1 style={{ marginBottom: 24 }}>Sign in</h1>
      <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <label>
          <div style={{ marginBottom: 4 }}>Email</div>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
            style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
          />
        </label>
        <label>
          <div style={{ marginBottom: 4 }}>Password</div>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
          />
        </label>
        {error && <div style={{ color: "crimson" }}>{error}</div>}
        <button type="submit" disabled={submitting} style={{ padding: "10px 16px", marginTop: 8 }}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p style={{ marginTop: 16, fontSize: 14, color: "#666" }}>
        {config?.signup_open === false
          ? "Signup is restricted — contact an admin."
          : (
            <>
              Don&apos;t have an account? <Link href={signupHref}>Sign up</Link>
            </>
          )}
      </p>
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
