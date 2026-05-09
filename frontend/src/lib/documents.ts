import useSWR from "swr";

import type { DocumentActivityResponse } from "@/types";

export function useDocumentActivity(path: string | null) {
  const key = path ? `/documents/file/activity?path=${encodeURIComponent(path)}` : null;
  const { data, error, isLoading, mutate } = useSWR<DocumentActivityResponse>(key);
  return {
    agents: data?.agents ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}
