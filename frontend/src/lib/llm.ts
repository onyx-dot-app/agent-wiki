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

export type Provider =
  | "anthropic"
  | "openai"
  | "gemini"
  | "ollama"
  | "custom"
  | "bedrock";

export const ALL_PROVIDERS: Provider[] = [
  "anthropic",
  "openai",
  "gemini",
  "ollama",
  "custom",
  "bedrock",
];

export const PROVIDER_LABELS: Record<Provider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
  ollama: "Ollama",
  custom: "Custom",
  bedrock: "Amazon Bedrock",
};

// Shape of GET /admin/llm (LLMView) — shared by the admin pages that render it.
export interface LLMSettings {
  provider: Provider;
  model: string;
  anthropic_api_key_set: boolean;
  openai_api_key_set: boolean;
  gemini_api_key_set: boolean;
  anthropic_api_key_hint: string;
  openai_api_key_hint: string;
  gemini_api_key_hint: string;
  ollama_base_url: string;
  custom_api_key_set: boolean;
  custom_api_key_hint: string;
  custom_base_url: string;
  custom_display_name: string;
  bedrock_aws_region: string;
  bedrock_endpoint_url: string;
  bedrock_aws_access_key_id_set: boolean;
  bedrock_aws_access_key_id_hint: string;
  bedrock_aws_secret_access_key_set: boolean;
  bedrock_aws_secret_access_key_hint: string;
  bedrock_aws_session_token_set: boolean;
  provider_models: Record<string, string[]>;
  ingest_selector_model: string;
}

export function isConfigured(p: Provider, s: LLMSettings): boolean {
  if (p === "anthropic") return s.anthropic_api_key_set;
  if (p === "openai") return s.openai_api_key_set;
  if (p === "gemini") return s.gemini_api_key_set;
  if (p === "ollama") return !!s.ollama_base_url;
  if (p === "custom") return !!s.custom_base_url;
  if (p === "bedrock") return !!s.bedrock_aws_region;
  return false;
}

export function providerLabel(p: Provider, s: LLMSettings): string {
  if (p === "custom") return s.custom_display_name || PROVIDER_LABELS.custom;
  return PROVIDER_LABELS[p];
}
