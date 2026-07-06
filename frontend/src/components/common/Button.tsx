"use client";

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type CSSProperties,
} from "react";

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
  {
    /* raw-ok: this is the legacy button wrapper itself */
  }
  return <button ref={ref} disabled={disabled} style={merged} {...rest} />;
});

function sizeStyle(size: ButtonSize): CSSProperties {
  if (size === "sm") {
    return {
      padding: "6px 12px",
      fontSize: 12,
      borderRadius: "var(--border-radius-04)",
    };
  }
  return {
    padding: "8px 14px",
    fontSize: 13,
    borderRadius: "var(--border-radius-08)",
  };
}

function variantStyle(variant: ButtonVariant): CSSProperties {
  switch (variant) {
    case "primary":
      return {
        background: "var(--background-tint-inverted-00)",
        color: "var(--text-inverted-05)",
        border: "1px solid var(--background-tint-inverted-00)",
      };
    case "danger":
      return {
        background: "var(--background-tint-00)",
        color: "var(--status-text-error-05)",
        border: "1px solid var(--status-error-02)",
      };
    case "ghost":
      return {
        background: "transparent",
        color: "var(--text-05)",
        border: "1px solid transparent",
      };
    case "secondary":
    default:
      return {
        background: "var(--background-tint-00)",
        color: "var(--text-05)",
        border: "1px solid var(--border-01)",
      };
  }
}
