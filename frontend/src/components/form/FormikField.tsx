"use client";

import {
  useField,
  type FieldInputProps,
  type FieldHelperProps,
  type FieldMetaProps,
} from "formik";
import React, { useMemo, memo } from "react";
import type { FormFieldState } from "./types";

export interface FormikFieldProps<T = any> {
  name: string;
  render: (
    field: FieldInputProps<T>,
    helper: FieldHelperProps<T>,
    meta: FieldMetaProps<T>,
    status: FormFieldState,
  ) => React.ReactElement;
}

function FormikFieldComponent<T>({ name, render }: FormikFieldProps<T>) {
  const [field, meta, helper] = useField<T>(name);

  const state = useMemo(
    (): FormFieldState =>
      meta.touched ? (meta.error ? "error" : "success") : "idle",
    [meta.touched, meta.error],
  );

  return render(field, helper, meta, state);
}

export const FormikField = memo(
  FormikFieldComponent,
) as typeof FormikFieldComponent;
