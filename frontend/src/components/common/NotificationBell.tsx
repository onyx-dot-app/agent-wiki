"use client";

import { useState, type ReactNode } from "react";

import {
  Button,
  LineItemButton,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import { SvgBell } from "@onyx-ai/opal/icons";

import { toast } from "@/hooks/useToast";
import {
  dismissAllNotifications,
  dismissNotification,
  notifLink,
  useNotifications,
  type NotificationView,
} from "@/lib/notifications";

export function NotificationBell() {
  const { notifications, undismissedCount, error, refresh } =
    useNotifications();
  const [open, setOpen] = useState(false);

  // Notifications are a general feature; if the endpoint ever 404s, fail
  // closed and render nothing rather than a broken bell.
  if (error) return null;

  function openLink(link: string) {
    if (/^https?:\/\//.test(link)) {
      window.open(link, "_blank", "noopener,noreferrer");
    } else if (link.startsWith("/") && !link.startsWith("//")) {
      // Same-tab nav for internal absolute paths only. Reject schemes
      // (javascript:/data:) and protocol-relative (//host) links — a
      // malformed notification record must not run script or escape origin.
      window.location.href = link;
    }
  }

  function onItemClick(n: NotificationView) {
    if (!n.dismissed) {
      void dismissNotification(n.id)
        .then(() => refresh())
        .catch(() => toast.error("Couldn't dismiss the notification."));
    }
    const link = notifLink(n);
    if (link) openLink(link);
    setOpen(false);
  }

  function row(n: NotificationView) {
    return (
      <LineItemButton
        key={n.id}
        title={n.title}
        description={n.description ?? undefined}
        sizePreset="main-ui"
        variant="section"
        rounding="sm"
        onClick={() => onItemClick(n)}
      />
    );
  }

  const unread = notifications.filter((n) => !n.dismissed);
  const read = notifications.filter((n) => n.dismissed);

  // PopoverMenu takes an array of nodes; a `null` entry renders a divider.
  const items: ReactNode[] = [
    <div key="hdr" className="flex items-center justify-between px-2 pt-1">
      <Text font="secondary-body" color="text-04">
        Notifications
      </Text>
      {undismissedCount > 0 && (
        <Button
          size="sm"
          prominence="tertiary"
          onClick={() =>
            void dismissAllNotifications()
              .then(() => refresh())
              .catch(() => toast.error("Couldn't mark notifications read."))
          }
        >
          Mark all read
        </Button>
      )}
    </div>,
    null,
  ];
  if (notifications.length === 0) {
    items.push(
      <div key="empty" className="p-2">
        <Text font="secondary-body" color="text-03">
          No notifications yet.
        </Text>
      </div>,
    );
  } else {
    unread.forEach((n) => items.push(row(n)));
    if (unread.length && read.length) items.push(null);
    read.forEach((n) => items.push(row(n)));
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div className="relative">
          <Button
            icon={SvgBell}
            prominence="tertiary"
            tooltip="Notifications"
            aria-label={`Notifications${
              undismissedCount ? ` (${undismissedCount} unread)` : ""
            }`}
            onClick={() => setOpen((o) => !o)}
          />
          {undismissedCount > 0 && (
            <span className="pointer-events-none absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-(--status-error-05) px-1 text-[10px] leading-none font-semibold text-(--text-inverted-05)">
              {undismissedCount > 9 ? "9+" : undismissedCount}
            </span>
          )}
        </div>
      </Popover.Anchor>
      <Popover.Content
        width="lg"
        align="end"
        sideOffset={6}
        container={typeof document !== "undefined" ? document.body : undefined}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <PopoverMenu>{items}</PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}
