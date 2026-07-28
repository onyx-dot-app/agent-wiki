/** Local icons for glyphs Opal does not ship yet. Each follows Opal's
 * IconProps contract (size + currentColor) so it can move upstream verbatim. */

import type { SVGProps } from "react";

interface IconProps extends SVGProps<SVGSVGElement> {
  size?: number;
}

/** The mock's `list` glyph: three lines with leading dots (1855:283373). */
export function SvgListLines({ size = 16, ...props }: IconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      {...props}
    >
      <path
        d="M5.33333 4H14M5.33333 8H14M5.33333 12H14M2 4H2.00667M2 8H2.00667M2 12H2.00667"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
