import type { SVGProps } from "react";

/** Send (paper plane) icon from the Onyx UI Library Figma icons — absent
 * from @onyx-ai/opal's published icon set. Same stroke API as library
 * icons; swap to the library export when it ships. */
export function SvgSend(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path
        d="M14.6667 1.33333L7.33333 8.66667M7.33333 8.66667L1.33333 6L14.6667 1.33333L10 14.6667L7.33333 8.66667Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
