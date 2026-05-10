"use client";

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type CSSProperties,
} from "react";

import { color, radius } from "@/lib/theme";

// Button variants used across the app:
//
// - primary  — near-black accent surface; one per row at most
// - secondary — neutral surface with a subtle border; the default
// - danger   — destructive action; uses state.danger tokens
// - ghost    — transparent surface; for low-emphasis actions in dense
//              chrome (icon-only toolbars usually want their own bespoke
//              button rather than this; ghost is for text actions)
//
// Sizes:
//
// - sm — 6/12 padding, 12px font; for dense rows / inline actions
// - md — 8/14 padding, 13px font; the default for forms and modals

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "secondary", size = "md", style, disabled, ...rest },
  ref,
) {
  const merged: CSSProperties = {
    ...sizeStyle(size),
    ...variantStyle(variant),
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
    fontWeight: 600,
    lineHeight: 1.2,
    transition: "background 80ms ease, border-color 80ms ease",
    ...style,
  };
  return <button ref={ref} disabled={disabled} style={merged} {...rest} />;
});

function sizeStyle(size: ButtonSize): CSSProperties {
  if (size === "sm") {
    return { padding: "6px 12px", fontSize: 12, borderRadius: radius.sm };
  }
  return { padding: "8px 14px", fontSize: 13, borderRadius: radius.md };
}

function variantStyle(variant: ButtonVariant): CSSProperties {
  switch (variant) {
    case "primary":
      return {
        background: color.accent.bg,
        color: color.accent.fg,
        border: `1px solid ${color.accent.bg}`,
      };
    case "danger":
      return {
        background: color.bg.page,
        color: color.state.danger.fg,
        border: `1px solid ${color.state.danger.border}`,
      };
    case "ghost":
      return {
        background: "transparent",
        color: color.text.primary,
        border: "1px solid transparent",
      };
    case "secondary":
    default:
      return {
        background: color.bg.page,
        color: color.text.primary,
        border: `1px solid ${color.border.default}`,
      };
  }
}
