"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";

// Mirrors app/models/notifications.py.
export interface NotificationView {
  id: number;
  notif_type: string;
  title: string;
  description: string | null;
  dismissed: boolean;
  first_shown: string;
  last_shown: string;
  data: Record<string, unknown>;
}

export interface NotificationList {
  notifications: NotificationView[];
  total_items: number;
  undismissed_count: number;
  has_more: boolean;
}

/**
 * The per-user notification center feed.
 *
 * Polls every 5s only while `activePoll` is true (i.e. a Craft launch is in
 * flight on the page) so the "Craft is ready" notification surfaces promptly;
 * otherwise it refreshes on focus/navigation only — no idle polling.
 */
export function useNotifications(opts: { activePoll?: boolean } = {}) {
  const { data, error, isLoading, mutate } = useSWR<NotificationList>(
    SWR_KEYS.notifications,
    { refreshInterval: opts.activePoll ? 5000 : 0 },
  );
  return {
    notifications: data?.notifications ?? [],
    undismissedCount: data?.undismissed_count ?? 0,
    isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  };
}

export function dismissNotification(id: number): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/notifications/${id}/dismiss`, {
    method: "POST",
  });
}

export function dismissAllNotifications(): Promise<{ dismissed: number }> {
  return apiFetch<{ dismissed: number }>("/notifications/dismiss-all", {
    method: "POST",
  });
}

/** Read a string field from a notification's free-form `data` payload. */
export function notifLink(n: NotificationView): string | null {
  const link = n.data?.link;
  return typeof link === "string" ? link : null;
}
