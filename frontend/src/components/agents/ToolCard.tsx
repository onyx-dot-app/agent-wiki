"use client";

import styles from "./ToolCard.module.css";

interface Props {
  id: string;
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
}

export function ToolCard({
  id,
  name,
  tagline,
  iconUrl,
  selected,
  onSelect,
}: Props) {
  void id;
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
    </button>
  );
}
