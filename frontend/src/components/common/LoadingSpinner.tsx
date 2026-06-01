import styles from "./LoadingSpinner.module.css";

// Canonical loading indicator. Replaces literal "Loading…" text everywhere.
// Bare animated ring by default; pass `label` for a ring+text row, `center`
// to fill and center its container (full-page / panel fallbacks).
export function LoadingSpinner({
  size = 16,
  label,
  center = false,
}: {
  size?: number;
  label?: string;
  center?: boolean;
}) {
  const wrapped = Boolean(label) || center;

  const ring = (
    <span
      role={wrapped ? undefined : "status"}
      aria-label={wrapped ? undefined : "Loading"}
      aria-hidden={wrapped ? true : undefined}
      className={styles.ring}
      style={{ width: size, height: size }}
    />
  );

  if (!wrapped) return ring;

  // A single status region: visible label (if any) is the accessible name,
  // else fall back to "Loading"; the ring itself is decorative here.
  return (
    <span
      role="status"
      aria-label={label ? undefined : "Loading"}
      className={center ? `${styles.wrap} ${styles.center}` : styles.wrap}
    >
      {ring}
      {label ? <span>{label}</span> : null}
    </span>
  );
}
