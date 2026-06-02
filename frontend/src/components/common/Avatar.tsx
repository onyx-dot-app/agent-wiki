import styles from "./Avatar.module.css";

type IconComponent = (props: { size?: number }) => React.ReactNode;

interface AvatarProps {
  /** Initials (1–2 chars) shown inside the circle. Ignored when `icon` is set. */
  label: string;
  /** Optional glyph rendered instead of the label (e.g. a group icon). */
  icon?: IconComponent;
  /** Diameter in px. Defaults to 28 (the share-row size). */
  size?: number;
  /** Accessible label / hover tooltip (e.g. the full name). */
  title?: string;
}

/** Round avatar — near-black accent fill, inverse text/glyph. Matches the
 * Figma share/transfer rows (no uploaded avatars exist server-side). Shows
 * `icon` (e.g. a group glyph) when provided, otherwise the initials. */
export function Avatar({ label, icon: Icon, size = 28, title }: AvatarProps) {
  return (
    <span
      className={styles.avatar}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
      title={title}
      role={title ? "img" : undefined}
      aria-label={title}
    >
      {Icon ? <Icon size={Math.round(size * 0.55)} /> : label}
    </span>
  );
}
