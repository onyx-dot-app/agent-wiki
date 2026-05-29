import styles from "./Avatar.module.css";

interface AvatarProps {
  /** Initials (1–2 chars) shown inside the circle. */
  label: string;
  /** Diameter in px. Defaults to 28 (the share-row size). */
  size?: number;
  /** Accessible label / hover tooltip (e.g. the full name). */
  title?: string;
}

/** Round initials avatar — near-black accent fill, inverse text. Matches
 * the Figma share/transfer rows (no uploaded avatars exist server-side). */
export function Avatar({ label, size = 28, title }: AvatarProps) {
  return (
    <span
      className={styles.avatar}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
      title={title}
      role={title ? "img" : undefined}
      aria-label={title}
    >
      {label}
    </span>
  );
}
