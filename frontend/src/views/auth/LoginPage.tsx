"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Formik, Form } from "formik";
import { Button, Text } from "@onyx-ai/opal/components";
import { SvgSimpleLoader } from "@onyx-ai/opal/icons";
import { useAuth } from "@/lib/auth";
import {
  AuthCard,
  AuthEmailField,
  AuthErrorBanner,
  AuthLayout,
  AuthPageSuspense,
  AuthPasswordField,
  LOGIN_VALIDATION_SCHEMA,
} from "@/views/auth/shared";

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
      {isOidc ? (
        <AuthCard
          submit={
            // Full-page navigation is required for the OIDC handshake, so
            // this uses a native <a> styled to look like the primary action button.
            <a
              href="/api/auth/oidc/login"
              className="box-border block w-full rounded-(--border-radius-08) border border-(--background-tint-inverted-00) bg-(--background-tint-inverted-00) px-3.5 py-2 text-center text-[13px] leading-[1.2] font-semibold text-(--text-inverted-05) no-underline"
            >
              Sign in with Google
            </a>
          }
        >
          {oidcErrorMessage && <AuthErrorBanner message={oidcErrorMessage} />}
        </AuthCard>
      ) : (
        <Formik<LoginValues>
          initialValues={INITIAL_VALUES}
          validationSchema={LOGIN_VALIDATION_SCHEMA}
          validateOnMount
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
          {({ isSubmitting, status, isValid }) => (
            <Form>
              <AuthCard
                submit={
                  <Button
                    type="submit"
                    width="full"
                    disabled={isSubmitting || !isValid}
                    icon={isSubmitting ? SvgSimpleLoader : undefined}
                  >
                    Sign in
                  </Button>
                }
              >
                {oidcErrorMessage && (
                  <AuthErrorBanner message={oidcErrorMessage} />
                )}
                <AuthEmailField autoFocus />
                <AuthPasswordField />
                {status?.error && <AuthErrorBanner message={status.error} />}
              </AuthCard>
            </Form>
          )}
        </Formik>
      )}

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
