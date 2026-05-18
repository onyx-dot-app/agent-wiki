"use client";

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
      <label className={styles.label} htmlFor="working-dir-input">
        Working directory
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
        <input
          type="checkbox"
          checked={remember}
          onChange={(e) => onRememberChange(e.target.checked)}
        />
        {pageHasBinding
          ? "Update default for this page"
          : "Remember as default for this page"}
      </label>
    </div>
  );
}
