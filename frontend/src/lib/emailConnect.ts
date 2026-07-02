import {
  createDestinationConfig,
  type DestinationConfig,
} from "@/lib/triggers";

/** Reuse the config for this address or create it (which sends the verify
 * link). Returns the id plus any verification-send error to surface. */
export async function ensureEmailDestination(
  configs: DestinationConfig[],
  address: string,
): Promise<{ id: string; verificationError: string | null }> {
  const normalized = address.trim().toLowerCase();
  const existing = configs.find(
    (c) =>
      c.type === "email" &&
      String(c.config.address ?? "").toLowerCase() === normalized,
  );
  if (existing) return { id: existing.id, verificationError: null };
  const created = await createDestinationConfig({
    type: "email",
    name: normalized,
    config: { address: normalized },
  });
  return {
    id: created.id,
    verificationError: created.verification_error ?? null,
  };
}
