import useSWR from "swr";

export interface LLMStatus {
  configured: boolean;
  provider: string;
}

export function useLLMStatus(opts: { skip?: boolean } = {}) {
  const { data, error, isLoading, mutate } = useSWR<LLMStatus>(
    opts.skip ? null : "/llm/status",
  );
  return {
    status: data,
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}
