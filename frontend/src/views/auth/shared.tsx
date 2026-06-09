"use client";

import { Suspense, type ReactNode } from "react";
import { Card } from "@onyx-ai/opal/components";
import { Content, InputVertical } from "@onyx-ai/opal/layouts";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
import { SvgSimpleLoader } from "@opal/icons";
import InputTypeInField from "@/components/form/InputTypeInField";

const MIN_PASSWORD_LENGTH = 8;

export interface AuthPageSuspenseProps {
  children: ReactNode;
}

export function AuthPageSuspense({ children }: AuthPageSuspenseProps) {
  return (
    <Suspense
      fallback={
        <main className="flex h-full items-center justify-center p-8">
          <SvgSimpleLoader />
        </main>
      }
    >
      {children}
    </Suspense>
  );
}

export interface AuthLayoutProps {
  children: ReactNode;
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-[400px]">{children}</div>
    </main>
  );
}

export interface AuthCardProps {
  children: ReactNode;
}

export function AuthCard({ children }: AuthCardProps) {
  return (
    <Card padding="lg" border="solid" rounding="lg">
      <div className="mb-6">
        <Content
          icon={SvgOnyxLogo}
          title="Welcome to Agent Wiki"
          description="Your open source AI agent collaboration platform"
        />
      </div>
      {children}
    </Card>
  );
}

export interface AuthErrorBannerProps {
  message: string;
}

export function AuthErrorBanner({ message }: AuthErrorBannerProps) {
  return (
    <div className="rounded-04 border border-status-error-02 bg-status-error-01 px-3 py-2.5 text-[13px] text-status-text-error-05">
      {message}
    </div>
  );
}

export interface AuthEmailFieldProps {
  autoFocus?: boolean;
}

export function AuthEmailField({ autoFocus }: AuthEmailFieldProps) {
  return (
    <InputVertical title="Email" withLabel>
      <InputTypeInField
        name="email"
        type="email"
        required
        autoFocus={autoFocus}
      />
    </InputVertical>
  );
}

export interface AuthPasswordFieldProps {
  lengthHint?: boolean;
}

export function AuthPasswordField({ lengthHint }: AuthPasswordFieldProps) {
  return (
    <InputVertical
      title="Password"
      subDescription={lengthHint ? "At least 8 characters" : undefined}
      withLabel
    >
      <InputTypeInField
        name="password"
        type="password"
        required
        minLength={MIN_PASSWORD_LENGTH}
      />
    </InputVertical>
  );
}
