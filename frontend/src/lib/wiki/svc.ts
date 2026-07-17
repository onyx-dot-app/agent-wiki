import { apiFetch } from "@/lib/api";
import type {
  FileDiffResponse,
  FileHistoryResponse,
  GeneratedDraft,
} from "@/lib/wiki/types";

export async function generateDraft(prompt: string): Promise<GeneratedDraft> {
  return apiFetch<GeneratedDraft>("/wiki/generate", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

/** Apply an instruction to an unsaved draft body; returns the revised body. */
export async function reviseDraft(
  body: string,
  instruction: string,
): Promise<{ body: string }> {
  return apiFetch<{ body: string }>("/wiki/revise", {
    method: "POST",
    body: JSON.stringify({ body, instruction }),
  });
}

export async function fetchFileDiff(
  path: string,
  sha: string,
): Promise<FileDiffResponse> {
  return apiFetch<FileDiffResponse>(
    `/wiki/file/diff?path=${encodeURIComponent(path)}&sha=${encodeURIComponent(
      sha,
    )}`,
  );
}

export async function fetchFileHistory(
  path: string,
): Promise<FileHistoryResponse> {
  return apiFetch<FileHistoryResponse>(
    `/wiki/file/history?path=${encodeURIComponent(path)}`,
  );
}
