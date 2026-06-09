"use client";

import React from "react";
import { useField } from "formik";
import { cn } from "@opal/utils";
import { Checkbox, Tooltip } from "@opal/components";

interface LabeledCheckboxFieldProps {
  name: string;
  label: string;
  labelClassName?: string;
  sublabel?: string;
  size?: "sm" | "md" | "lg";
  tooltip?: string;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
}

export const LabeledCheckboxField: React.FC<LabeledCheckboxFieldProps> = ({
  name,
  label,
  onChange,
  sublabel,
  size = "md",
  tooltip,
  labelClassName,
  disabled,
  ...props
}) => {
  const [field, , helpers] = useField<boolean>({ name, type: "checkbox" });

  const sizeClasses = {
    sm: "h-2 w-2",
    md: "h-3 w-3",
    lg: "h-4 w-4",
  };

  const handleClick = (e: React.MouseEvent<HTMLLabelElement>) => {
    e.preventDefault();
    const next = !field.value;
    helpers.setValue(next);
    onChange?.(next);
  };

  const labelId = `${name}-label`;

  const checkboxContent = (
    <div className="flex w-fit items-start space-x-2">
      <Checkbox
        id={name}
        aria-labelledby={labelId}
        checked={field.value}
        onCheckedChange={(checked) => {
          helpers.setValue(Boolean(checked));
          onChange?.(Boolean(checked));
        }}
        className={cn(sizeClasses[size])}
        disabled={disabled}
        {...props}
      />
      <div className="flex flex-col">
        <label
          id={labelId}
          htmlFor={name}
          className="flex cursor-pointer flex-col"
          onClick={handleClick}
        >
          <span
            className={cn(
              "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
              labelClassName,
            )}
          >
            {label}
          </span>
          {sublabel && (
            <span className="mt-1 text-sm text-text-03">{sublabel}</span>
          )}
        </label>
      </div>
    </div>
  );

  return (
    <Tooltip tooltip={tooltip} side="top" sideOffset={25}>
      {checkboxContent}
    </Tooltip>
  );
};

export default LabeledCheckboxField;
