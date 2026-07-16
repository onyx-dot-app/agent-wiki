/** Shared types for the wiki route views (`WikiPage`, `Explorer`, `NewDocView`). */

export interface DocEntry {
  path: string;
  updated_at: string;
}

/** Full flat listing of every wiki doc — the `/wiki` response, used to derive
 * folder trees and directory listings. */
export interface ListResponse {
  entries: DocEntry[];
}
