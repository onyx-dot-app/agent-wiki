"use client";

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type FunctionComponent,
} from "react";

import type { IconProps } from "@onyx-ai/opal/types";
import { cn } from "@onyx-ai/opal/utils";

/**
 * Onyx-style Button. Wraps a raw ``<button>`` element with the
 * variant × prominence class system defined in
 * ``src/app/css/button.css`` and a size → padding / rounding mapping.
 * The label is rendered through a plain ``<span>`` with the matching
 * ``-text`` class.
 *
 * Variants (mutually exclusive): ``main`` / ``action`` / ``danger``.
 * Prominence (mutually exclusive): ``primary`` / ``secondary`` /
 * ``tertiary`` / ``internal``. Defaults: ``main`` + ``primary``.
 *
 * Sizes: ``"lg"`` (default) = ``rounded-12`` 12-px corners, the standard
 * modal / page-action size. ``"md"`` = ``rounded-08`` 8-px corners, used
 * for dense rows.
 */

export interface OnyxButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  main?: boolean;
  action?: boolean;
  danger?: boolean;

  primary?: boolean;
  secondary?: boolean;
  tertiary?: boolean;
  internal?: boolean;

  transient?: boolean;
  size?: "lg" | "md";

  leftIcon?: FunctionComponent<IconProps>;
  rightIcon?: FunctionComponent<IconProps>;
}

const SIZE_CLASS_MAP = {
  lg: {
    button: "p-2 rounded-12 gap-1.5",
    text: "text-text-04 font-medium text-sm leading-none",
    content: { left: "pr-1", right: "pl-1", none: "" },
  },
  md: {
    button: "p-1 rounded-08 gap-0",
    text: "text-text-04 font-medium text-xs leading-none",
    content: { left: "pr-1 py-0.5", right: "pl-1 py-0.5", none: "py-0.5" },
  },
} as const;

export const OnyxButton = forwardRef<HTMLButtonElement, OnyxButtonProps>(
  function OnyxButton(
    {
      main,
      action,
      danger,
      primary,
      secondary,
      tertiary,
      internal,
      transient,
      size = "lg",
      leftIcon: LeftIcon,
      rightIcon: RightIcon,
      type = "button",
      className,
      children,
      disabled,
      ...rest
    },
    ref,
  ) {
    if (LeftIcon && RightIcon)
      throw new Error("OnyxButton: cannot specify both leftIcon and rightIcon");

    const variant = action ? "action" : danger ? "danger" : "main";
    const subvariant = secondary
      ? "secondary"
      : tertiary
        ? "tertiary"
        : internal
          ? "internal"
          : "primary";

    void main; // accepted for symmetry with onyx API; "main" is the default.

    const buttonClass = `button-${variant}-${subvariant}`;
    const textClass = `button-${variant}-${subvariant}-text`;
    const iconClass = `button-${variant}-${subvariant}-icon`;
    const iconPlacement: "left" | "right" | "none" = LeftIcon
      ? "left"
      : RightIcon
        ? "right"
        : "none";
    const sizeClasses = SIZE_CLASS_MAP[size];

    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled}
        data-state={transient ? "transient" : undefined}
        className={cn(
          "h-fit w-fit flex flex-row items-center justify-center",
          sizeClasses.button,
          buttonClass,
          className,
        )}
        {...rest}
      >
        {LeftIcon && (
          <div className="w-4 h-4 flex flex-col items-center justify-center">
            <LeftIcon className={cn("w-4 h-4", iconClass)} />
          </div>
        )}
        {children !== "" && (
          <div
            className={cn("leading-none", sizeClasses.content[iconPlacement])}
          >
            {typeof children === "string" ? (
              <span
                className={cn("whitespace-nowrap", sizeClasses.text, textClass)}
              >
                {children}
              </span>
            ) : (
              children
            )}
          </div>
        )}
        {RightIcon && (
          <div className="w-4 h-4">
            <RightIcon className={cn("w-4 h-4", iconClass)} />
          </div>
        )}
      </button>
    );
  },
);
