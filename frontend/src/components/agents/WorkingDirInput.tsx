"use client";

import { Checkbox, Text } from "@onyx-ai/opal/components";
import { Label, Section } from "@onyx-ai/opal/layouts";

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
    <Section
      flexDirection="column"
      alignItems="start"
      justifyContent="start"
      gap={1.5}
      width="full"
    >
      <Label label="working-dir-input">
        <Text font="secondary-action" color="text-04">
          Working directory
        </Text>
      </Label>
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
      <Label>
        <Section
          flexDirection="row"
          alignItems="center"
          justifyContent="start"
          gap={1.5}
        >
          <Checkbox checked={remember} onCheckedChange={onRememberChange} />
          <Text font="secondary-body" color="text-03">
            {pageHasBinding
              ? "Update default for this page"
              : "Remember as default for this page"}
          </Text>
        </Section>
      </Label>
    </Section>
  );
}
