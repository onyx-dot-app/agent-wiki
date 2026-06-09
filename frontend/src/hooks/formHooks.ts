"use client";

import { useField } from "formik";

export function useOnChangeEvent<T = any>(
  name: string,
  f?: (event: T) => void,
) {
  const [field, , helpers] = useField<T>(name);
  return (event: T) => {
    helpers.setTouched(true);
    f?.(event);
    field.onChange(event);
  };
}

export function useOnChangeValue<T = any>(
  name: string,
  f?: (value: T) => void,
) {
  const [, , helpers] = useField<T>(name);
  return (value: T) => {
    helpers.setTouched(true);
    f?.(value);
    helpers.setValue(value);
  };
}

export function useOnBlurEvent<T = any>(name: string, f?: (event: T) => void) {
  const [field] = useField<T>(name);
  return (event: T) => {
    f?.(event);
    field.onBlur(event);
  };
}
