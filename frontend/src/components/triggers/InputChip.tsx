"use client";

import { Button, Tag } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** A removable selection inside an input chip bar: Opal Tag plus a 2xs clear
 * button. FilterButton is unsuitable here — its selected state is the dark
 * filter-bar look and its light state swaps the clear for a chevron. */
export function InputChip({
  icon,
  label,
  onRemove,
  disabled,
  title,
}: {
  icon: IconFunctionComponent;
  label: string;
  onRemove?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <span className="flex shrink-0 items-center gap-[2px]" title={title}>
      <Tag icon={icon} title={label} />
      {onRemove && (
        <Button
          type="button"
          icon={SvgX}
          size="2xs"
          prominence="tertiary"
          tooltip="Remove"
          onClick={onRemove}
          disabled={disabled}
        />
      )}
    </span>
  );
}
