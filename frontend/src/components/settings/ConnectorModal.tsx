"use client";

import { Button, Text } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { Content, ContentAction } from "@onyx-ai/opal/layouts";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** Shared chrome for the Settings connector modals (Emails, Webhooks): the
 * mock's 480px three-zone alert. Scrim, header with icon + close, a raised
 * content panel the caller fills, and a Done footer. Opal ships no Modal
 * primitive, so the shell lives here once. */
export function ConnectorModalShell({
  icon,
  title,
  description,
  onClose,
  banner,
  children,
}: {
  icon: IconFunctionComponent;
  title: string;
  description: string;
  onClose: () => void;
  /** Rendered above the card, inside the scrim (e.g. a "check your inbox" toast). */
  banner?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-(--mask-03) backdrop-blur-[2px]"
      onClick={onClose}
    >
      {banner}
      <div
        className="flex max-h-[92vh] w-[min(480px,92vw)] flex-col overflow-y-auto rounded-(--radius-16) border border-(--border-01) bg-(--background-tint-01) shadow-(--shadow-modal)"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative w-full rounded-t-(--radius-16) bg-(--background-tint-00) p-4">
          <Content
            sizePreset="section"
            variant="heading"
            icon={icon}
            title={title}
            description={description}
          />
          <span className="absolute top-2 right-2">
            <Button
              type="button"
              icon={SvgX}
              size="sm"
              prominence="tertiary"
              tooltip="Close"
              onClick={onClose}
            />
          </span>
        </div>

        <div className="w-full flex-1 p-4">
          <div className="flex w-full flex-col gap-2 rounded-(--radius-12) bg-(--background-tint-00) p-2">
            {children}
          </div>
        </div>

        <div className="flex h-[68px] w-full items-center justify-end rounded-b-(--radius-16) bg-(--background-tint-00) p-4">
          <Button type="button" prominence="secondary" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}

/** One registered destination inside a connector modal: raised row card with
 * icon, name, status line, and right-side actions (children). */
export function ConfigRowCard({
  icon,
  title,
  description,
  children,
}: {
  icon: IconFunctionComponent;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="w-full rounded-(--radius-08) bg-(--background-tint-01) p-[6px]">
      <ContentAction
        sizePreset="main-ui"
        variant="section"
        icon={icon}
        title={title}
        description={description}
        rightChildren={
          <span className="flex shrink-0 items-center gap-1">{children}</span>
        }
      />
    </div>
  );
}

/** The row cards' small inline action (Verify, Test). Slot overrides shrink
 * Opal's Button below its xs metrics to fit the 6px row padding. */
export function MiniActionButton({
  icon,
  disabled,
  onClick,
  children,
}: {
  icon: IconFunctionComponent;
  disabled?: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <span className="flex items-center [&_button]:!h-6 [&_button]:!rounded-(--radius-08) [&_button]:!border-0 [&_button]:!bg-(--background-tint-00) [&_button_span]:!text-[12px] [&_button_span]:!leading-4">
      <Button
        type="button"
        size="xs"
        prominence="secondary"
        icon={icon}
        disabled={disabled}
        onClick={onClick}
      >
        {children}
      </Button>
    </span>
  );
}

/** Centered "N Things" count between two rules, closing the config list. */
export function CountDivider({ count, noun }: { count: number; noun: string }) {
  return (
    <div className="flex w-full items-center gap-2 px-4 py-2">
      <span className="h-0 min-w-px flex-1 border-t border-(--border-01)" />
      <Text font="secondary-body" color="text-03" nowrap>
        {`${count} ${noun}${count === 1 ? "" : "s"}`}
      </Text>
      <span className="h-0 min-w-px flex-1 border-t border-(--border-01)" />
    </div>
  );
}
