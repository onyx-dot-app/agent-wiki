import useSWR from "swr";

import { SWR_KEYS } from "@/lib/swr-keys";
import type { DocumentActivityResponse } from "@/types";

export function useDocumentActivity(path: string | null) {
  const key = path ? SWR_KEYS.documentActivity(path) : null;
  const { data, error, isLoading, mutate } =
    useSWR<DocumentActivityResponse>(key);
  return {
    agents: data?.agents ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}
