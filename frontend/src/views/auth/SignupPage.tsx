"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Formik, Form } from "formik";
import { Button, Text } from "@onyx-ai/opal/components";
import { InputVertical } from "@onyx-ai/opal/layouts";
import { useAuth } from "@/lib/auth";
import InputTypeInField from "@/components/form/InputTypeInField";
import {
  AuthCard,
  AuthEmailField,
  AuthErrorBanner,
  AuthLayout,
  AuthPageSuspense,
  AuthPasswordField,
  SIGNUP_VALIDATION_SCHEMA,
} from "@/views/auth/shared";

interface SignupValues {
  email: string;
  name: string;
  password: string;
}

const INITIAL_VALUES: SignupValues = {
  email: "",
  name: "",
  password: "",
};

function SignupForm() {
  const { signup, config } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  // OIDC mode has no password; bounce to /login where the SSO button lives.
  useEffect(() => {
    if (config?.mode === "oidc") {
      const next = params?.get("next");
      router.replace(
        next ? `/login?next=${encodeURIComponent(next)}` : "/login",
      );
    }
  }, [config?.mode, params, router]);

  const next = params?.get("next") ?? null;
  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  return (
    <AuthLayout>
      <AuthCard>
        <Formik<SignupValues>
          initialValues={INITIAL_VALUES}
          validationSchema={SIGNUP_VALIDATION_SCHEMA}
          onSubmit={async (values, { setStatus }) => {
            setStatus(null);
            try {
              await signup(
                values.email,
                values.password,
                values.name || undefined,
              );
              router.replace(next ?? "/");
            } catch (err) {
              setStatus({
                error: err instanceof Error ? err.message : "signup failed",
              });
            }
          }}
        >
          {({ isSubmitting, status }) => (
            <Form className="flex flex-col gap-2">
              <AuthEmailField autoFocus />
              <InputVertical title="Name" suffix="optional" withLabel>
                <InputTypeInField name="name" type="text" />
              </InputVertical>
              <AuthPasswordField lengthHint />
              {status?.error && <AuthErrorBanner message={status.error} />}
              <div className="mt-1">
                <Button
                  type="submit"
                  variant="action"
                  width="full"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Creating…" : "Create account"}
                </Button>
              </div>
            </Form>
          )}
        </Formik>
      </AuthCard>
      <div className="flex w-full items-baseline justify-center">
        <Text font="secondary-body" color="text-03">
          Already have an account?
        </Text>
        <Link href={loginHref} className="underline">
          <Text font="secondary-body">Sign in</Text>
        </Link>
      </div>
    </AuthLayout>
  );
}

export default function SignupPage() {
  return (
    <AuthPageSuspense>
      <SignupForm />
    </AuthPageSuspense>
  );
}
