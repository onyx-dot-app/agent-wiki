import { apiFetch } from "@/lib/api";
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
