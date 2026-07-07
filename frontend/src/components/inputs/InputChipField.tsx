"use client";

import * as React from "react";
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
      className={cn(
        "flex min-h-9 w-full cursor-text flex-row flex-wrap items-center gap-1 rounded-(--radius-08) p-1.5",
        "border border-(--border-02) bg-(--background-neutral-00) focus-within:border-(--border-05) focus-within:shadow-[0_0_0_2px_var(--background-tint-04)]",
        disabled && "cursor-not-allowed opacity-50",
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
      {/* raw-ok: InputChipField's own inner input, per the upstream component */}
      <input
        ref={inputRef}
        type="text"
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={onFocus}
        placeholder={placeholder}
        className="h-6 min-w-[80px] flex-1 bg-transparent p-0.5 text-[14px] leading-5 text-(--text-04) outline-none placeholder:text-(--text-02)"
      />
    </div>
  );
}
