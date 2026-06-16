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

// Partial update: include only the field(s) you want to change. Omitted fields
// keep their (possibly inherited) state; sending a field as `null` clears it
// back to inherit. So toggling auto-update never disturbs the instruction, and
// vice versa.
export interface UpdatePolicyPatch {
  ingestion_auto_update_disabled?: boolean | null;
  update_instruction?: string | null;
}

export function getUpdatePolicy(path: string): Promise<UpdatePolicyResponse> {
  return apiFetch<UpdatePolicyResponse>(
    `/update-policy?path=${encodeURIComponent(path)}`,
  );
}

export function patchUpdatePolicy(
  path: string,
  patch: UpdatePolicyPatch,
): Promise<UpdatePolicyResponse> {
  return apiFetch<UpdatePolicyResponse>("/update-policy", {
    method: "PATCH",
    body: JSON.stringify({ path, ...patch }),
  });
}
