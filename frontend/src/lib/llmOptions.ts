// Flat, selectable model list built from /api/llm/available, shaped like the
// onyx model selector's options module so the selector component is shared
// structure rather than a lookalike.
import type { FunctionComponent } from "react";

import useSWR from "swr";

import {
  SvgAnthropic,
  SvgAws,
  SvgGemini,
  SvgOllama,
  SvgOpenai,
} from "@onyx-ai/opal/logos";
import { SvgOnyxOctagon } from "@onyx-ai/opal/icons";
import type { IconProps } from "@onyx-ai/opal/types";

import { SWR_KEYS } from "@/lib/swr-keys";

export interface AvailableProvider {
  provider: string;
  label: string;
  models: string[];
}

export interface LLMOption {
  provider: string;
  providerDisplayName: string;
  modelName: string;
  displayName: string;
}

export interface LLMOptionGroup {
  key: string;
  displayName: string;
  options: LLMOption[];
  Icon: FunctionComponent<IconProps>;
}

const PROVIDER_ICONS: Record<string, FunctionComponent<IconProps>> = {
  anthropic: SvgAnthropic,
  openai: SvgOpenai,
  gemini: SvgGemini,
  ollama: SvgOllama,
  bedrock: SvgAws,
};

export function getModelIcon(provider: string): FunctionComponent<IconProps> {
  return PROVIDER_ICONS[provider.toLowerCase()] ?? SvgOnyxOctagon;
}

export function llmOptionKey(option: {
  provider: string;
  modelName: string;
}): string {
  return `${option.provider}:${option.modelName}`;
}

/** "gpt-5.5" reads as "GPT-5.5", "claude-sonnet-4.6" as "Claude Sonnet 4.6".
 *  Bedrock ids nest the model behind region and vendor namespaces, which are
 *  stripped ("us.anthropic.claude-sonnet-4-6-v1:0" renders Claude Sonnet 4 6). */
export function displayModelName(id: string | null): string {
  if (!id) return "";
  const tail = (id.split("/").pop() ?? id)
    .replace(/^(?:[a-z]{2}\.)?[a-z]+\.(?=[a-z].*-)/, "")
    .replace(/:\d+$/, "")
    .replace(/-v\d+$/, "");
  return tail
    .split("-")
    .map((seg) =>
      /^gpt$/i.test(seg)
        ? "GPT"
        : /^\d/.test(seg)
          ? seg
          : seg.charAt(0).toUpperCase() + seg.slice(1),
    )
    .join(" ")
    .replace(/^GPT (\d)/, "GPT-$1");
}

/** Flattens provider descriptors into a deduplicated list of selectable
 *  models. The endpoint only returns providers with credentials configured. */
export function buildLlmOptions(
  providers: AvailableProvider[] | undefined,
): LLMOption[] {
  if (!providers) return [];

  const seenKeys = new Set<string>();
  const options: LLMOption[] = [];

  providers.forEach((provider) => {
    provider.models.forEach((modelName) => {
      const key = llmOptionKey({ provider: provider.provider, modelName });
      if (seenKeys.has(key)) return;
      seenKeys.add(key);

      options.push({
        provider: provider.provider,
        providerDisplayName: provider.label,
        modelName,
        displayName: displayModelName(modelName),
      });
    });
  });

  return options;
}

/** Groups a flat list of model options by provider, sorted alphabetically by
 *  display name. */
export function groupLlmOptions(
  filteredOptions: LLMOption[],
): LLMOptionGroup[] {
  const groups = new Map<string, Omit<LLMOptionGroup, "key">>();

  filteredOptions.forEach((option) => {
    const groupKey = option.provider.toLowerCase();

    if (!groups.has(groupKey)) {
      groups.set(groupKey, {
        displayName: option.providerDisplayName,
        options: [],
        Icon: getModelIcon(option.provider),
      });
    }

    groups.get(groupKey)!.options.push(option);
  });

  const sortedKeys = Array.from(groups.keys()).sort((a, b) =>
    groups.get(a)!.displayName.localeCompare(groups.get(b)!.displayName),
  );

  return sortedKeys.map((key) => {
    const group = groups.get(key)!;
    return {
      key,
      displayName: group.displayName,
      options: group.options,
      Icon: group.Icon,
    };
  });
}

/** Providers with credentials configured, straight from /llm/available. */
export function useLlmAvailable() {
  const { data, isLoading } = useSWR<{ providers: AvailableProvider[] }>(
    SWR_KEYS.llmAvailable,
  );
  return { providers: data?.providers, isLoading };
}
