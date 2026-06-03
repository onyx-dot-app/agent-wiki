"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useAuth } from "@/lib/auth";

function SignupForm() {
  const { signup, config } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  // OIDC mode has no password; bounce to /login where the SSO button lives.
  useEffect(() => {
    if (config?.mode === "oidc") {
      const next = params.get("next");
      router.replace(
        next ? `/login?next=${encodeURIComponent(next)}` : "/login",
      );
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
    <main className="flex min-h-screen items-center justify-center bg-(--background-tint-02) p-6">
      <div className="w-full max-w-[400px] rounded-(--border-radius-12) border border-(--border-01) bg-(--background-tint-00) p-8 shadow-(--shadow-md)">
        <h1 className="m-0 mb-1.5 text-[22px] text-(--text-05)">
          Create account
        </h1>
        <p className="m-0 mb-6 text-sm text-(--text-03)">
          Set up your workspace login.
        </p>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <label className="text-[13px] text-(--text-04)">
            <div className="mb-1.5">Email</div>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-2.5 py-2 text-sm text-(--text-05) outline-none"
            />
          </label>
          <label className="text-[13px] text-(--text-04)">
            <div className="mb-1.5">Name (optional)</div>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-2.5 py-2 text-sm text-(--text-05) outline-none"
            />
          </label>
          <label className="text-[13px] text-(--text-04)">
            <div className="mb-1.5">Password</div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-2.5 py-2 text-sm text-(--text-05) outline-none"
            />
            <div className="mt-1.5 text-xs text-(--text-03)">
              At least 8 characters.
            </div>
          </label>
          {error && (
            <div className="rounded-(--border-radius-04) border border-(--status-error-02) bg-(--status-error-01) px-3 py-2.5 text-[13px] text-(--status-text-error-05)">
              {error}
            </div>
          )}
          <div className="mt-1">
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
        <p className="mt-5 mb-0 text-[13px] text-(--text-03)">
          Already have an account?{" "}
          <Link href={loginHref} className="text-(--text-05) underline">
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
        <main className="p-8">
          <LoadingSpinner center />
        </main>
      }
    >
      <SignupForm />
    </Suspense>
  );
}
