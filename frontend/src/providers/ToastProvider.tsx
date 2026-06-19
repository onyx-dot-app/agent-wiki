"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import { Button, MessageCard, Text } from "@onyx-ai/opal/components";
import { cn } from "@onyx-ai/opal/utils";

import {
  toast,
  toastStore,
  MAX_VISIBLE_TOASTS,
  TOAST_ANIMATION_MS,
} from "@/hooks/useToast";
import type { Toast, ToastLevel } from "@/hooks/useToast";
const MAX_TOAST_MESSAGE_LENGTH = 150;
// How long a toast lingers after the user clicks to expand it. Long enough to
// read a multi-line stack trace / API error without forcing a manual dismiss.
const EXPANDED_DURATION_MS = 30000;

type MessageCardVariant = React.ComponentProps<typeof MessageCard>["variant"];

const LEVEL_TO_VARIANT: Record<ToastLevel, MessageCardVariant> = {
  success: "success",
  error: "error",
  warning: "warning",
  info: "info",
  default: "default",
};

function buildDescription(t: Toast): string | undefined {
  return t.description ? t.description : undefined;
}

interface ExpandedDetailsProps {
  message: string;
}

function ExpandedDetails({ message }: ExpandedDetailsProps) {
  return (
    <div className="max-h-72 overflow-y-auto px-3 py-2 wrap-break-word whitespace-pre-wrap">
      <Text font="secondary-body" color="text-03" as="p">
        {message}
      </Text>
    </div>
  );
}

function ToastAction({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <div className="px-3 pb-2">
      <Button type="button" size="sm" variant="action" onClick={onClick}>
        {label}
      </Button>
    </div>
  );
}

function ToastContainer() {
  const allToasts = useSyncExternalStore(
    toastStore.subscribe,
    toastStore.getSnapshot,
    toastStore.getSnapshot,
  );
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const visible = allToasts.slice(-MAX_VISIBLE_TOASTS);

  const handleClose = useCallback((id: string) => {
    toast._markLeaving(id);
    setTimeout(() => {
      toast.dismiss(id);
      setExpandedIds((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }, TOAST_ANIMATION_MS);
  }, []);

  const handleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    // Reset the auto-dismiss timer so the user has time to read the full
    // message before it fades.
    toast.setAutoDismiss(id, EXPANDED_DURATION_MS);
  }, []);

  if (visible.length === 0) return null;

  return (
    <div
      data-testid="toast-container"
      role="status"
      aria-live="polite"
      aria-atomic="false"
      className="fixed right-4 bottom-4 z-(--z-toast) flex w-full max-w-(--toast-width) flex-col items-end gap-2"
    >
      {visible.map((t) => {
        const isTruncatable = t.message.length > MAX_TOAST_MESSAGE_LENGTH;
        const isExpanded = expandedIds.has(t.id);
        const truncatedTitle = isTruncatable
          ? t.message.slice(0, MAX_TOAST_MESSAGE_LENGTH) + "…"
          : t.message;
        const expandable = isTruncatable && !isExpanded;
        return (
          <div
            key={t.id}
            className={cn(
              "w-full",
              t.leaving ? "animate-fade-out-scale" : "animate-fade-in-scale",
              expandable && "cursor-pointer",
            )}
            onClick={
              expandable
                ? (e) => {
                    // Don't intercept clicks on the inner close button.
                    if (
                      (e.target as HTMLElement).closest(
                        'button[aria-label="Close"]',
                      )
                    ) {
                      return;
                    }
                    handleExpand(t.id);
                  }
                : undefined
            }
          >
            <MessageCard
              variant={LEVEL_TO_VARIANT[t.level ?? "info"]}
              title={truncatedTitle}
              description={buildDescription(t)}
              padding="xs"
              onClose={t.dismissible ? () => handleClose(t.id) : undefined}
              bottomChildren={
                isExpanded ? (
                  <ExpandedDetails message={t.message} />
                ) : t.action ? (
                  <ToastAction
                    label={t.action.label}
                    onClick={() => {
                      t.action?.onClick();
                      handleClose(t.id);
                    }}
                  />
                ) : undefined
              }
            />
          </div>
        );
      })}
    </div>
  );
}

export { ToastContainer };
