// Folder and file icons for the wiki explorer.
//
// "Solid" pair: the folder is a fully filled silhouette in muted text
// color and the file is an outlined sheet with a folded corner. The
// strong fill-vs-stroke contrast keeps the two readable at any size
// while staying inside the warm-neutral palette (no amber/blue).

import { color } from "@/lib/theme";

interface IconProps {
  size?: number;
}

export function FolderIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <path
        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        fill={color.text.muted}
      />
    </svg>
  );
}

export function FileIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <path
        d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"
        fill={color.bg.page}
        stroke={color.text.secondary}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M14 3v5h5"
        fill="none"
        stroke={color.text.secondary}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}
