"use client";

import React, { useId, useMemo } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "@onyx-ai/opal/utils";
import { FieldContext, useFieldContext } from "./FieldContext";
import type {
  APIMessageProps,
  ControlProps,
  DescriptionProps,
  FieldContextType,
  FormFieldRootProps,
  LabelProps,
  MessageProps,
} from "./types";

export const FormFieldRoot: React.FC<FormFieldRootProps> = ({
  id,
  name,
  state = "idle",
  required,
  className,
  children,
  ...props
}) => {
  const reactId = useId();
  const baseId = id ?? `field_${reactId}`;

  const describedByIds = useMemo(
    () => [`${baseId}-desc`, `${baseId}-msg`, `${baseId}-api-msg`],
    [baseId],
  );

  const contextValue: FieldContextType = {
    baseId,
    name,
    required,
    state,
    describedByIds,
  };

  return (
    <FieldContext.Provider value={contextValue}>
      <div
        id={baseId}
        className={cn("flex flex-col gap-y-1", className)}
        {...props}
      >
        {children}
      </div>
    </FieldContext.Provider>
  );
};

export const FormFieldLabel: React.FC<LabelProps> = ({
  leftIcon,
  rightIcon,
  optional,
  required,
  rightAction,
  className,
  children,
  ...props
}) => {
  const { baseId } = useFieldContext();
  return (
    <label
      id={`${baseId}-label`}
      htmlFor={`${baseId}-control`}
      className={cn(
        "ml-0.5 flex flex-row items-center gap-1 text-[13px] font-medium text-text-04",
        className,
      )}
      {...props}
    >
      {leftIcon && <span className="flex items-center">{leftIcon}</span>}
      {children}
      {required ? (
        <span className="mx-0.5 text-[12px] text-text-03">(Required)</span>
      ) : optional ? (
        <span className="mx-0.5 text-[12px] text-text-03">(Optional)</span>
      ) : null}
      {rightIcon && <span className="flex items-center">{rightIcon}</span>}
      {rightAction && (
        <span className="ml-auto flex items-center">{rightAction}</span>
      )}
    </label>
  );
};

export const FormFieldControl: React.FC<ControlProps> = ({
  asChild,
  children,
}) => {
  const { baseId, state, describedByIds, required } = useFieldContext();

  const ariaAttributes = {
    id: `${baseId}-control`,
    "aria-invalid": state === "error",
    "aria-describedby": describedByIds?.join(" "),
    "aria-required": required,
  };

  if (asChild) {
    return <Slot {...ariaAttributes}>{children}</Slot>;
  }

  if (React.isValidElement(children)) {
    return React.cloneElement(children, {
      ...ariaAttributes,
      ...(children.props as any),
    });
  }

  return <>{children}</>;
};

export const FormFieldDescription: React.FC<DescriptionProps> = ({
  className,
  children,
  ...props
}) => {
  const { baseId } = useFieldContext();
  if (!children) return null;
  return (
    <p
      id={`${baseId}-desc`}
      className={cn("ml-0.5 text-[13px] text-text-03", className)}
      {...props}
    >
      {children}
    </p>
  );
};

export const FormFieldMessage: React.FC<MessageProps> = ({
  className,
  messages,
}) => {
  const { baseId, state } = useFieldContext();
  let tempState = state;
  let content = messages?.[tempState];
  if (tempState === "success" && !content) {
    tempState = "idle";
    content = messages?.idle;
  }
  if (!content) return null;
  return (
    <p
      id={`${baseId}-msg`}
      className={cn(
        "ml-0.5 text-[13px]",
        tempState === "error" && "text-status-text-error-05",
        tempState === "success" && "text-status-text-success-05",
        tempState === "idle" && "text-text-03",
        className,
      )}
    >
      {content}
    </p>
  );
};

export const FormAPIFieldMessage: React.FC<APIMessageProps> = ({
  className,
  messages,
  state = "loading",
}) => {
  const { baseId } = useFieldContext();
  const content = messages?.[state];
  if (!content) return null;
  return (
    <p
      id={`${baseId}-api-msg`}
      className={cn(
        "ml-0.5 text-[13px]",
        state === "error" && "text-status-text-error-05",
        state === "success" && "text-status-text-success-05",
        (state === "idle" || state === "loading") && "text-text-03",
        className,
      )}
    >
      {content}
    </p>
  );
};

export const FormField = Object.assign(FormFieldRoot, {
  Label: FormFieldLabel,
  Control: FormFieldControl,
  Description: FormFieldDescription,
  Message: FormFieldMessage,
  APIMessage: FormAPIFieldMessage,
});
