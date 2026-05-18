"use client";

import { SelectCard, Text } from "@onyx-ai/opal/components";

interface Props {
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
}

/**
 * Thin aggregate over OPAL's SelectCard — same select-card visual the
 * rest of the app uses, plus the launcher-specific icon + name +
 * tagline body.
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
      <div className="flex items-center gap-2.5 w-full">
        <img src={iconUrl} alt="" width={24} height={24} />
        <div className="flex flex-col min-w-0">
          <Text font="main-ui-body" color="text-04" nowrap>
            {name}
          </Text>
          <Text font="secondary-body" color="text-03" nowrap>
            {tagline}
          </Text>
        </div>
      </div>
    </SelectCard>
  );
}
