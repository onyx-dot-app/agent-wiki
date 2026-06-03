// Folder and file icons for the wiki explorer.
import { SvgDocFile, SvgFolder } from "@onyx-ai/opal/icons";
import { color } from "@/lib/theme";

interface IconProps {
  size?: number;
}

export function FolderIcon({ size = 20 }: IconProps) {
  return (
    <span style={{ color: color.text.muted, display: "flex" }}>
      <SvgFolder size={size} />
    </span>
  );
}

export function FileIcon({ size = 20 }: IconProps) {
  return (
    <span style={{ color: color.text.secondary, display: "flex" }}>
      <SvgDocFile size={size} />
    </span>
  );
}
