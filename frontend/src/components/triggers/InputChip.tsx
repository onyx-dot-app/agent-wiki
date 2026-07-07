"use client";

import { Button, Text } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** A removable selection inside an input chip bar, per the Input/Tags spec:
 * a tinted radius-08 container holding an optional 16px icon, main-ui-body
 * text, and an internal remove button inside the chip. Opal's Tag has no
 * remove and FilterButton's states are the filter-bar look, so the container
 * is composed here from Text and an internal-prominence Button. */
export function InputChip({
  icon: Icon,
  label,
  onRemove,
  disabled,
  title,
}: {
  icon?: IconFunctionComponent;
  label: string;
  onRemove?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <span
      className="flex shrink-0 items-center rounded-(--radius-08) bg-(--background-tint-02) px-1 py-[2px]"
      title={title}
    >
      {Icon && (
        <span className="flex size-4 items-center justify-center">
          <Icon className="size-3.5 text-(--text-04)" />
        </span>
      )}
      <span className="max-w-[160px] px-[2px]">
        <Text font="main-ui-body" color="text-04" nowrap maxLines={1}>
          {label}
        </Text>
      </span>
      {onRemove && (
        <Button
          type="button"
          icon={SvgX}
          size="2xs"
          prominence="internal"
          tooltip="Remove"
          onClick={onRemove}
          disabled={disabled}
        />
      )}
    </span>
  );
}
