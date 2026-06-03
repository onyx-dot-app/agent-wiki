import { SvgOnyxLogo } from "@onyx-ai/opal/logos";

/** Two-tone "Agent Wiki" wordmark shown at the top of the sidebars. */
export function Wordmark() {
  return (
    <span className="flex items-center gap-2">
      <SvgOnyxLogo size={28} />
      <span className="text-[20px] tracking-tight">
        <span className="font-bold text-text-05">Agent</span>{" "}
        <span className="font-normal text-text-03">Wiki</span>
      </span>
    </span>
  );
}
