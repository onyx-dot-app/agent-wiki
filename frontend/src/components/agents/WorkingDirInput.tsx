"use client";

import { Checkbox } from "@onyx-ai/opal/components";
import { InputHorizontal, InputVertical } from "@onyx-ai/opal/layouts";

import styles from "./WorkingDirInput.module.css";

interface Props {
  value: string;
  onChange: (v: string) => void;
  remember: boolean;
  onRememberChange: (v: boolean) => void;
  pageHasBinding: boolean;
}

export function WorkingDirInput({
  value,
  onChange,
  remember,
  onRememberChange,
  pageHasBinding,
}: Props) {
  return (
    <>
      <InputVertical title="Working directory" withLabel="working-dir-input">
        <input
          id="working-dir-input"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="(leave blank for scratch directory)"
          autoComplete="off"
          spellCheck={false}
          className={styles.input}
        />
      </InputVertical>
      <InputHorizontal
        title={
          pageHasBinding
            ? "Update default for this page"
            : "Remember as default for this page"
        }
        withLabel
        center
      >
        <Checkbox checked={remember} onCheckedChange={onRememberChange} />
      </InputHorizontal>
    </>
  );
}
