"use client";

import * as React from "react";
import { cn } from "@onyx-ai/opal/utils";

export interface InputTextAreaProps extends Omit<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "disabled"
> {
  variant?: "primary" | "disabled";
  resizable?: boolean;
}

/** Port of Onyx's refresh-components InputTextArea (styled multiline input),
 * trimmed to the variants agent-wiki uses and carrying the standard input
 * border/focus treatment. Swap to the library version when it ships in
 * @onyx-ai/opal. */
const InputTextArea = React.forwardRef<HTMLTextAreaElement, InputTextAreaProps>(
  function InputTextArea(
    { variant = "primary", className, rows = 4, resizable = true, ...props },
    ref,
  ) {
    const disabled = variant === "disabled";
    return (
      <textarea
        ref={ref}
        rows={rows}
        disabled={disabled}
        className={cn(
          "box-border w-full rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) px-[10px] py-2 text-[14px] leading-5 text-(--text-04) outline-none",
          "placeholder:text-(--text-02) focus:border-(--border-05) focus:shadow-[0_0_0_2px_var(--background-tint-04)]",
          resizable ? "resize-y" : "resize-none",
          disabled && "cursor-not-allowed opacity-50",
          className,
        )}
        {...props}
      />
    );
  },
);

export default InputTextArea;
