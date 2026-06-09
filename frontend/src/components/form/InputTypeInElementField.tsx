"use client";

import { useField } from "formik";
import {
  Button,
  InputTypeIn,
  type InputTypeInProps,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgMinusCircle } from "@onyx-ai/opal/icons";
import { useOnChangeEvent, useOnBlurEvent } from "@/hooks/formHooks";

export interface InputTypeInElementFieldProps extends Omit<
  InputTypeInProps,
  "value"
> {
  name: string;
  onRemove?: () => void;
}

export default function InputTypeInElementField({
  name,
  onRemove,
  onChange: onChangeProp,
  onBlur: onBlurProp,
  ...inputProps
}: InputTypeInElementFieldProps) {
  const [field, meta] = useField(name);
  const onChange = useOnChangeEvent(name, onChangeProp);
  const onBlur = useOnBlurEvent(name, onBlurProp);
  const hasError = meta.touched && meta.error;
  const isEmpty = !field.value || field.value.trim() === "";
  const isNonEditable =
    inputProps.variant === "disabled" || inputProps.variant === "readOnly";

  return (
    <Section flexDirection="row" gap={0.25}>
      <InputTypeIn
        {...inputProps}
        id={name}
        name={name}
        value={field.value ?? ""}
        onChange={onChange}
        onBlur={onBlur}
        variant={
          isNonEditable
            ? inputProps.variant
            : hasError
              ? "error"
              : inputProps.variant
        }
      />
      <Button
        disabled={!onRemove || isEmpty}
        icon={SvgMinusCircle}
        prominence="tertiary"
        onClick={onRemove}
        tooltip="Remove"
      />
    </Section>
  );
}
