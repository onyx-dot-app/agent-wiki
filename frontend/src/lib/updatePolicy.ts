// Typed client for the per-page/per-folder update policy
// (`/api/update-policy`). The policy has two fields — whether ingestion
// auto-update is disabled, and a free-text update instruction — resolved
// most-granular-wins on the server. `effective` is what's actually in force
// (incl. inheritance); `explicit` is the row set on exactly this path.
import { apiFetch } from "@/lib/api";

export interface EffectivePolicy {
  ingestion_auto_update_disabled: boolean;
  update_instruction: string | null;
}

export interface ExplicitPolicy {
  path: string;
  kind: "page" | "folder";
  ingestion_auto_update_disabled: boolean | null;
  update_instruction: string | null;
  updated_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface UpdatePolicyResponse {
  explicit: ExplicitPolicy | null;
  effective: EffectivePolicy;
}

export interface SetUpdatePolicyInput {
  ingestion_auto_update_disabled: boolean | null;
  update_instruction: string | null;
}

export function getUpdatePolicy(path: string): Promise<UpdatePolicyResponse> {
  return apiFetch<UpdatePolicyResponse>(
    `/update-policy?path=${encodeURIComponent(path)}`,
  );
}

// PUT is full desired-state: both fields are sent every time, so callers pass
// the complete intended policy (a missing field clears it on the server).
export function setUpdatePolicy(
  path: string,
  input: SetUpdatePolicyInput,
): Promise<UpdatePolicyResponse> {
  return apiFetch<UpdatePolicyResponse>("/update-policy", {
    method: "PUT",
    body: JSON.stringify({ path, ...input }),
  });
}
