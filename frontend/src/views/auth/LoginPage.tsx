"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Formik, Form } from "formik";
import { Button, Text } from "@onyx-ai/opal/components";
import { useAuth } from "@/lib/auth";
import {
  AuthCard,
  AuthEmailField,
  AuthErrorBanner,
  AuthLayout,
  AuthPageSuspense,
  AuthPasswordField,
} from "./shared";

const OIDC_ERROR_MESSAGES: Record<string, string> = {
  oidc_exchange_failed: "Couldn't complete sign-in. Try again.",
  oidc_userinfo_failed:
    "Couldn't fetch your profile from the identity provider.",
  oidc_no_email: "The identity provider didn't return an email address.",
  oidc_email_unverified:
    "Your email isn't verified with the identity provider.",
  oidc_email_not_allowed:
    "Your email isn't on the allow list for this workspace.",
};

interface LoginValues {
  email: string;
  password: string;
}

const INITIAL_VALUES: LoginValues = {
  email: "",
  password: "",
};

function LoginForm() {
  const { login, config } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  const next = params?.get("next") ?? null;
  const signupHref = next
    ? `/signup?next=${encodeURIComponent(next)}`
    : "/signup";
  const oidcError = params?.get("error") ?? null;
  const oidcErrorMessage = oidcError
    ? (OIDC_ERROR_MESSAGES[oidcError] ?? oidcError)
    : null;
  const isOidc = config?.mode === "oidc";

  return (
    <AuthLayout>
      <AuthCard>
        {oidcErrorMessage && (
          <div className="mb-4">
            <AuthErrorBanner message={oidcErrorMessage} />
          </div>
        )}
        {isOidc ? (
          // Native <a> so the browser performs a full navigation to the
          // OIDC start endpoint. Styled to match Button variant="primary"
          // size="md" — keep in sync with components/common/Button.tsx.
          <a
            href="/api/auth/oidc/login"
            className="box-border block w-full rounded-(--border-radius-08) border border-(--background-tint-inverted-00) bg-(--background-tint-inverted-00) px-3.5 py-2 text-center text-[13px] leading-[1.2] font-semibold text-(--text-inverted-05) no-underline"
          >
            Sign in with Google
          </a>
        ) : (
          <Formik<LoginValues>
            initialValues={INITIAL_VALUES}
            onSubmit={async (values, { setStatus }) => {
              setStatus(null);
              try {
                await login(values.email, values.password);
                router.replace(next ?? "/");
              } catch (err) {
                setStatus({
                  error: err instanceof Error ? err.message : "login failed",
                });
              }
            }}
          >
            {({ isSubmitting, status }) => (
              <Form className="flex flex-col gap-2">
                <AuthEmailField autoFocus />
                <AuthPasswordField />
                {status?.error && <AuthErrorBanner message={status.error} />}
                <div className="mt-1">
                  <Button
                    type="submit"
                    variant="action"
                    width="full"
                    disabled={isSubmitting}
                  >
                    {isSubmitting ? "Signing in…" : "Sign in"}
                  </Button>
                </div>
              </Form>
            )}
          </Formik>
        )}
      </AuthCard>
      {!isOidc && (
        <div className="flex w-full items-baseline justify-center">
          {config?.signup_open === false ? (
            <Text font="secondary-body" color="text-03">
              Signup is restricted — contact an admin.
            </Text>
          ) : (
            <>
              <Text font="secondary-body" color="text-03">
                Don&apos;t have an account?
              </Text>
              <Link href={signupHref} className="underline">
                <Text font="secondary-body">Sign up</Text>
              </Link>
            </>
          )}
        </div>
      )}
    </AuthLayout>
  );
}

export default function LoginPage() {
  return (
    <AuthPageSuspense>
      <LoginForm />
    </AuthPageSuspense>
  );
}
