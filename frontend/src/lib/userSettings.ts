"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";
import type { UserSettings, UserSettingsUpdate } from "@/types";

export async function getUserSettings(): Promise<UserSettings> {
  return apiFetch<UserSettings>("/user/settings");
}

export async function updateUserSettings(
  partial: UserSettingsUpdate,
): Promise<UserSettings> {
  return apiFetch<UserSettings>("/user/settings", {
    method: "PUT",
    body: JSON.stringify(partial),
  });
}

/** The signed-in user's settings row, revalidated through one cache key. */
export function useUserSettings() {
  const { data, mutate } = useSWR<UserSettings>(
    SWR_KEYS.userSettings,
    getUserSettings,
  );
  return { settings: data, mutate };
}
