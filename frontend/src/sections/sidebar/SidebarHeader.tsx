"use client";

import { type ReactNode } from "react";
import { Text } from "@onyx-ai/opal/components";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";

interface SidebarHeaderProps {
  actions?: ReactNode;
}

export function SidebarHeader({ actions }: SidebarHeaderProps) {
  return (
    <div className="flex shrink-0 flex-row items-start justify-between px-2 pt-3">
      <div className="flex h-7 items-center gap-2 px-1">
        <SvgOnyxLogoTyped size={28} />
        <Text font="heading-h3" color="text-03">
          Wiki
        </Text>
      </div>
      {actions}
    </div>
  );
}
