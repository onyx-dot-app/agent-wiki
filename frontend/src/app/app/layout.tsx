"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { AppSidebar } from "@/sections/sidebar/AppSidebar";
import { useAuth } from "@/lib/auth";
import { useHealth } from "@/lib/health";
import { useLLMStatus } from "@/lib/llm";

const BANNER_HEALTH_POLL_MS = 15000;

const BANNER_CLASSES = {
  error: "bg-(--status-error-01) border-b border-(--status-error-02) text-(--status-text-error-05)",
  warning: "bg-(--status-warning-01) border-b border-(--status-warning-02) text-(--status-text-warning-05)",
} as const;

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-(--background-tint-01)">
      <AppSidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <StatusBanner />
        <div className="flex-1 min-w-0">{children}</div>
      </div>
    </div>
  );
}

function StatusBanner() {
  const { user, loading } = useAuth();
  const skip = loading || !user;
  const { health, error: healthError } = useHealth({
    refreshIntervalMs: skip ? undefined : BANNER_HEALTH_POLL_MS,
  });
  const { status: llmStatus } = useLLMStatus({ skip });

  const backendUnreachable = !skip && !!healthError;
  const backendDegraded = !skip && health?.status === "degraded";

  if (skip) return null;
  if (backendUnreachable || backendDegraded) {
    return (
      <BackendHealthBanner
        unreachable={backendUnreachable}
        isAdmin={!!user?.is_admin}
        message={healthError?.message ?? null}
      />
    );
  }
  if (llmStatus?.configured === false) {
    return <LLMSetupBanner isAdmin={!!user?.is_admin} />;
  }
  return null;
}

function BannerShell({ tone, children }: { tone: "warning" | "error"; children: ReactNode }) {
  return (
    <div role="alert" className={`flex items-center gap-3 py-2.5 px-4 text-sm ${BANNER_CLASSES[tone]}`}>
      <span aria-hidden className="text-base leading-none">⚠️</span>
      <span className="flex-1">{children}</span>
    </div>
  );
}

function BackendHealthBanner({
  unreachable,
  isAdmin,
  message,
}: {
  unreachable: boolean;
  isAdmin: boolean;
  message: string | null;
}) {
  return (
    <BannerShell tone="error">
      <strong>{unreachable ? "Backend unreachable." : "Backend degraded."}</strong>{" "}
      {unreachable
        ? "The frontend can't reach the backend. Some features will not work until it recovers."
        : "The backend isn't fully healthy. Background work like search indexing and scheduled triggers may be delayed until it recovers."}{" "}
      {isAdmin ? (
        <Link href="/admin/health" className="underline font-semibold">
          View health details
        </Link>
      ) : (
        <span>Please ask a workspace admin to investigate.</span>
      )}
      {message && isAdmin && (
        <span className="ml-2 text-xs opacity-80">({message})</span>
      )}
    </BannerShell>
  );
}

function LLMSetupBanner({ isAdmin }: { isAdmin: boolean }) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && sessionStorage.getItem("llm-banner-dismissed") === "1") {
      setDismissed(true);
    }
  }, []);

  if (dismissed) return null;

  return (
    <BannerShell tone="warning">
      <span className="flex items-center gap-3">
        <span className="flex-1">
          <strong>No language model is configured.</strong>{" "}
          {isAdmin ? (
            <>
              AI features are disabled until you add a provider and API key on the{" "}
              <Link
                href="/admin/language-models"
                className="text-(--status-text-warning-05) underline font-semibold"
              >
                LLM settings page
              </Link>
              .
            </>
          ) : (
            <>AI features are disabled. Please ask a workspace admin to finish setup.</>
          )}
        </span>
        <Button
          icon={SvgX}
          prominence="tertiary"
          size="sm"
          tooltip="Dismiss"
          onClick={() => {
            if (typeof window !== "undefined") sessionStorage.setItem("llm-banner-dismissed", "1");
            setDismissed(true);
          }}
        />
      </span>
    </BannerShell>
  );
}
