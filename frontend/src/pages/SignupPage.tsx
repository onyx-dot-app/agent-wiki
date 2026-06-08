"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button, Card, InputTypeIn } from "@onyx-ai/opal/components";
import { Content, InputVertical } from "@onyx-ai/opal/layouts";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
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
    <main className="flex min-h-screen items-center justify-center bg-background-tint-02 p-6">
      <div className="w-full max-w-[400px]">
        <Card padding="lg" border="solid">
          <div className="mb-6">
            <Content
              icon={SvgOnyxLogo}
              title="Welcome to Agent Wiki"
              description="Your open source AI agent collaboration platform"
            />
          </div>
          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <InputVertical title="Email" withLabel>
              <InputTypeIn
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
            </InputVertical>
            <InputVertical title="Name" suffix="optional" withLabel>
              <InputTypeIn
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </InputVertical>
            <InputVertical
              title="Password"
              description="At least 8 characters."
              withLabel
            >
              <InputTypeIn
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
              />
            </InputVertical>
            {error && (
              <div className="rounded-04 border border-status-error-02 bg-status-error-01 px-3 py-2.5 text-[13px] text-status-text-error-05">
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
          <p className="text--text-03) mt-5 mb-0 text-[13px]">
            Already have an account?{" "}
            <Link href={loginHref} className="text-text-05 underline">
              Sign in
            </Link>
          </p>
        </Card>
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
