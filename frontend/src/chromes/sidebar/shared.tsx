"use client";

import { Text } from "@onyx-ai/opal/components";
import { SvgOnyxLogo, SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** Logo factory for `SidebarLayouts.Header`'s `renderAppLogo`: the returned
 * component is rendered by the header at its own icon size. */
export function sidebarLogo(folded: boolean): IconFunctionComponent {
  return folded
    ? SvgOnyxLogo
    : ({ size }) => (
        <div className="flex items-center gap-2">
          <SvgOnyxLogoTyped size={size} />
          <Text font="heading-h3" color="text-03">
            Wiki
          </Text>
        </div>
      );
}
