"use client";

import { useMemo } from "react";

import { useNotifications, type NotificationView } from "@/lib/notifications";

import { useEvents } from "./hooks";
import { isNewActivity, toEventIso } from "./utils";
import type { AppEvent } from "./types";

/** One row of the Activities panel: an app event or a per-user
 *  notification, unified so the panel renders a single time-ordered feed. */
export type FeedItem =
  | {
      key: string;
      iso: string;
      unread: boolean;
      kind: "event";
      event: AppEvent;
    }
  | {
      key: string;
      iso: string;
      unread: boolean;
      kind: "notification";
      notification: NotificationView;
    };

/**
 * Merged events + notifications feed, newest first. Notifications are
 * unread until dismissed, and events count as new inside the 24h activity
 * window. Both backends emit zone-less UTC timestamps, normalized here.
 */
export function useActivityFeed() {
  const ev = useEvents({ limit: 100 }, { refreshInterval: 30_000 });
  const nf = useNotifications();

  const items = useMemo<FeedItem[]>(() => {
    const rows: FeedItem[] = [
      ...ev.events.map<FeedItem>((event) => ({
        key: `ev-${event.id}`,
        iso: toEventIso(event.ts),
        unread: isNewActivity(event.ts),
        kind: "event",
        event,
      })),
      ...nf.notifications.map<FeedItem>((notification) => ({
        key: `nf-${notification.id}`,
        iso: toEventIso(notification.last_shown),
        unread: !notification.dismissed,
        kind: "notification",
        notification,
      })),
    ];
    return rows.sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0));
  }, [ev.events, nf.notifications]);

  return {
    items,
    unreadCount: items.filter((i) => i.unread).length,
    isLoading: ev.isLoading || nf.isLoading,
    error: ev.error ?? nf.error,
    refresh: async () => {
      await Promise.all([ev.refresh(), nf.refresh()]);
    },
  };
}
