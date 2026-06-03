"use client";

import Link from "next/link";
import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useAuth } from "@/lib/auth";

const OIDC_ERROR_MESSAGES: Record<string, string> = {
  oidc_exchange_failed: "Couldn't complete sign-in. Try again.",
  oidc_userinfo_failed: "Couldn't fetch your profile from the identity provider.",
  oidc_no_email: "The identity provider didn't return an email address.",
  oidc_email_unverified: "Your email isn't verified with the identity provider.",
  oidc_email_not_allowed: "Your email isn't on the allow list for this workspace.",
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
    <main className="min-h-screen flex items-center justify-center p-6 bg-(--background-tint-02)">
      <div className="w-full max-w-[400px] bg-(--background-tint-00) border border-(--border-01) rounded-(--border-radius-12) p-8 shadow-(--shadow-md)">
        <h1 className="m-0 mb-1.5 text-[22px] text-(--text-05)">
          Sign in
        </h1>
        <p className="m-0 mb-6 text-sm text-(--text-03)">
          {isOidc ? "Continue with your workspace identity provider." : "Welcome back."}
        </p>

        {oidcErrorMessage && (
          <div className="mb-4 py-2.5 px-3 text-[13px] bg-(--status-error-01) border border-(--status-error-02) text-(--status-text-error-05) rounded-(--border-radius-04)">
            {oidcErrorMessage}
          </div>
        )}

        {isOidc ? (
          // Native <a> so the browser performs a full navigation to the
          // OIDC start endpoint. Styled to match Button variant="primary"
          // size="md" — keep in sync with components/common/Button.tsx.
          <a
            href="/api/auth/oidc/login"
            className="block w-full py-2 px-3.5 text-[13px] font-semibold leading-[1.2] text-center bg-(--background-tint-inverted-00) text-(--text-inverted-05) border border-(--background-tint-inverted-00) rounded-(--border-radius-08) no-underline box-border"
          >
            Sign in with Google
          </a>
        ) : (
          <>
            <form onSubmit={onSubmit} className="flex flex-col gap-3">
              <label className="text-[13px] text-(--text-04)">
                <div className="mb-1.5">Email</div>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoFocus
                  className="w-full py-2 px-2.5 text-sm border border-(--border-01) rounded-(--border-radius-04) bg-(--background-tint-00) text-(--text-05) box-border outline-none"
                />
              </label>
              <label className="text-[13px] text-(--text-04)">
                <div className="mb-1.5">Password</div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full py-2 px-2.5 text-sm border border-(--border-01) rounded-(--border-radius-04) bg-(--background-tint-00) text-(--text-05) box-border outline-none"
                />
              </label>
              {error && (
                <div className="py-2.5 px-3 text-[13px] bg-(--status-error-01) border border-(--status-error-02) text-(--status-text-error-05) rounded-(--border-radius-04)">
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
                  {submitting ? "Signing in…" : "Sign in"}
                </Button>
              </div>
            </form>
            <p className="mt-5 mb-0 text-[13px] text-(--text-03)">
              {config?.signup_open === false ? (
                "Signup is restricted — contact an admin."
              ) : (
                <>
                  Don&apos;t have an account?{" "}
                  <Link href={signupHref} className="text-(--text-05) underline">
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
    <Suspense
      fallback={
        <main className="p-8">
          <LoadingSpinner center />
        </main>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
