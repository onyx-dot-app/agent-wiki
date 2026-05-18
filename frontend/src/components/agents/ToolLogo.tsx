"use client";

import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import type { IconProps } from "@onyx-ai/opal/types";

const REGISTRY: Record<string, (props: IconProps) => React.JSX.Element> = {
  "claude-code": SvgClaude,
  codex: SvgOpenai,
  "onyx-craft": SvgOnyxLogo,
};

interface Props {
  toolId: string;
  size: number;
}

export function ToolLogo({ toolId, size }: Props) {
  const Logo = REGISTRY[toolId];
  if (!Logo) return null;
  return <Logo size={size} />;
}
