"use client";

import { ToolStatusBadge } from "./ToolStatusBadge";
import styles from "./ToolCard.module.css";

interface Props {
  id: string;
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
  tokenReady: boolean;
  helperReady: boolean;
  cliReady: boolean | null;
}

export function ToolCard({
  id,
  name,
  tagline,
  iconUrl,
  selected,
  onSelect,
  tokenReady,
  helperReady,
  cliReady,
}: Props) {
  const className = [
    styles.card,
    selected ? styles.selected : "",
    onSelect ? styles.clickable : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={className}
    >
      <div className={styles.header}>
        <img src={iconUrl} alt="" width={24} height={24} />
        <div className={styles.body}>
          <div className={styles.name}>{name}</div>
          <div className={styles.tagline}>{tagline}</div>
        </div>
      </div>
      <div className={styles.badges}>
        <ToolStatusBadge
          status={tokenReady ? "ok" : "warn"}
          label={tokenReady ? "Token" : "Need token"}
        />
        <ToolStatusBadge
          status={helperReady ? "ok" : "warn"}
          label={helperReady ? "Launcher" : "No launcher"}
        />
        <ToolStatusBadge
          status={cliReady === null ? "muted" : cliReady ? "ok" : "warn"}
          label={
            cliReady === null ? "CLI: ?" : cliReady ? "CLI" : `${id} missing`
          }
        />
      </div>
    </button>
  );
}
