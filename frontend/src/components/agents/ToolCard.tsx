"use client";

import { SelectCard, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { ToolLogo } from "./ToolLogo";

interface Props {
  toolId: string;
  name: string;
  tagline: string;
  selected: boolean;
  onSelect?: () => void;
}

export function ToolCard({ toolId, name, tagline, selected, onSelect }: Props) {
  return (
    <SelectCard
      state={selected ? "selected" : "empty"}
      onClick={onSelect}
      padding="sm"
      rounding="md"
      border="solid"
    >
      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="start"
        gap={0.625}
        width="full"
      >
        <ToolLogo toolId={toolId} size={24} />
        <Section
          flexDirection="column"
          alignItems="start"
          justifyContent="center"
          width="full"
          height="fit"
          gap={0.0625}
        >
          <Text font="main-ui-body" color="text-04" nowrap>
            {name}
          </Text>
          <Text font="secondary-body" color="text-03" nowrap>
            {tagline}
          </Text>
        </Section>
      </Section>
    </SelectCard>
  );
}
