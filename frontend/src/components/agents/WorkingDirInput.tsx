"use client";

import { Checkbox, Text } from "@onyx-ai/opal/components";

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
    <div className={styles.wrapper}>
      <label htmlFor="working-dir-input">
        <Text font="secondary-action" color="text-04">
          Working directory
        </Text>
      </label>
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
      <label className={styles.rememberRow}>
        <Checkbox checked={remember} onCheckedChange={onRememberChange} />
        <Text font="secondary-body" color="text-03">
          {pageHasBinding
            ? "Update default for this page"
            : "Remember as default for this page"}
        </Text>
      </label>
    </div>
  );
}
