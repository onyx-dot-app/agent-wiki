"use client";

import { type ReactNode } from "react";
import { Text } from "@onyx-ai/opal/components";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";

interface SidebarHeaderProps {
  actions?: ReactNode;
}

interface SidebarBodyProps {
  children: ReactNode;
}

interface SidebarFooterProps {
  children: ReactNode;
}

interface SidebarNavListProps {
  children: ReactNode;
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

export function SidebarBody({ children }: SidebarBodyProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-x-hidden px-2">
      {children}
    </div>
  );
}

export function SidebarFooter({ children }: SidebarFooterProps) {
  return (
    <div className="flex shrink-0 flex-col gap-px px-2">{children}</div>
  );
}

export function SidebarNavList({ children }: SidebarNavListProps) {
  return <div className="flex flex-col gap-px">{children}</div>;
}
