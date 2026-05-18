"use client";

import { SelectCard, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

interface Props {
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
}

/**
 * Aggregate over OPAL's SelectCard + Section + Text. The icon stays a
 * native <img> because OPAL has no equivalent for branded tool logos.
 */
export function ToolCard({
  name,
  tagline,
  iconUrl,
  selected,
  onSelect,
}: Props) {
  return (
    <SelectCard
      state={selected ? "selected" : "empty"}
      onClick={onSelect}
      padding="md"
      rounding="md"
      border="solid"
    >
      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="start"
        gap={2.5}
        width="full"
      >
        <img src={iconUrl} alt="" width={24} height={24} />
        <Section
          flexDirection="column"
          alignItems="start"
          justifyContent="center"
          width="full"
          height="fit"
          gap={0.5}
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
