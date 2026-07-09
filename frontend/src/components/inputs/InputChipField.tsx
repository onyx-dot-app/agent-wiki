"use client";

import * as React from "react";
import { InputTypeIn } from "@onyx-ai/opal/components";
import { cn } from "@onyx-ai/opal/utils";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import Chip from "@/components/inputs/Chip";

export interface ChipItem {
  id: string;
  label: string;
  /** When true the chip shows a warning icon. */
  error?: boolean;
}

export interface InputChipFieldProps {
  chips: ChipItem[];
  onRemoveChip: (id: string) => void;
  onAdd: (value: string) => void;

  value: string;
  onChange: (value: string) => void;

  placeholder?: string;
  disabled?: boolean;
  icon?: IconFunctionComponent;
  className?: string;
  /** Put the text input on its own full-width line below the chips instead
   * of inline, so a long placeholder is never clipped in the leftover gap. */
  inputBelow?: boolean;
  onFocus?: () => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

/** Port of Onyx's refresh-components InputChipField: chips inline with a
 * text input; Enter adds via onAdd, Backspace on empty removes the last
 * chip. The wrapper carries the standard input border/focus treatment
 * (agent-wiki has no input-normal utility class). Swap to the library
 * version when it ships in @onyx-ai/opal. */
export default function InputChipField({
  chips,
  onRemoveChip,
  onAdd,
  value,
  onChange,
  placeholder,
  disabled = false,
  icon: Icon,
  className,
  inputBelow = false,
  onFocus,
  onKeyDown,
}: InputChipFieldProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (disabled) return;
    onKeyDown?.(e);
    if (e.defaultPrevented) return;
    if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      const trimmed = value.trim();
      if (trimmed) onAdd(trimmed);
    }
    if (e.key === "Backspace" && value === "") {
      const lastChip = chips[chips.length - 1];
      if (lastChip) onRemoveChip(lastChip.id);
    }
  }

  return (
    <div
      data-variant={disabled ? "disabled" : "primary"}
      className={cn(
        "opal-input min-h-9 cursor-text flex-wrap !justify-start gap-1",
        className,
      )}
      onClick={() => inputRef.current?.focus()}
    >
      {chips.map((chip) => (
        <Chip
          key={chip.id}
          smallLabel={false}
          error={chip.error}
          onRemove={disabled ? undefined : () => onRemoveChip(chip.id)}
        >
          {chip.label}
        </Chip>
      ))}
      {Icon && <Icon className="size-4 shrink-0 text-(--text-04)" />}
      {/* raw-ok: Opal's .opal-input-field contract for a composite input's inner field; nested InputTypeIn double-pads past the 36px Input/Tags height */}
      <input
        ref={inputRef}
        type="text"
        className={cn(
          "opal-input-field",
          inputBelow ? "basis-full" : "min-w-[80px] flex-1",
        )}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={onFocus}
        placeholder={placeholder}
      />
    </div>
  );
}
