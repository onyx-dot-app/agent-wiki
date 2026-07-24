import { PEER_COLORS } from "@/lib/editor/constants";

/** Deterministically map a `userId` to a color from `PEER_COLORS` so a given
 * peer keeps the same color for the full session. */
export function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++)
    h = (h * 31 + userId.charCodeAt(i)) | 0;
  return PEER_COLORS[Math.abs(h) % PEER_COLORS.length]!;
}
