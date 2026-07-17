"use client";

import { Text } from "@onyx-ai/opal/components";
import { SvgOnyxLogo, SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";

export function sidebarLogo(folded: boolean | undefined) {
  return (
    <div className="flex items-center gap-2 px-1">
      {folded ? (
        <SvgOnyxLogo size={28} />
      ) : (
        <>
          <SvgOnyxLogoTyped size={28} />
          <Text font="heading-h3" color="text-03">
            Wiki
          </Text>
        </>
      )}
    </div>
  );
}
