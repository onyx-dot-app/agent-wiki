"use client";

import {
  LineItemButton,
  Popover,
  PopoverMenu,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import { SvgLogOut, SvgSettings, SvgUser } from "@onyx-ai/opal/icons";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";

// Account row + anchored popover menu (name/email header, Settings,
// Sign out). Shared by AppSidebar and AdminSidebar so the profile
// affordance behaves identically in both views — clicking the row
// never navigates directly; the menu disambiguates the intent.
export function UserMenu({
  folded = false,
  onNavigate,
}: {
  folded?: boolean;
  /** Called before menu-driven navigation (e.g. to close a mobile drawer). */
  onNavigate?: () => void;
}) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  const displayName = user?.name || user?.email || "";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {/* Anchor (not Trigger): SidebarTab doesn't forward refs — same
          pattern as ShareDialog's picker. */}
      <Popover.Anchor asChild>
        <div>
          <SidebarTab
            icon={SvgUser}
            folded={folded}
            tooltip={folded ? displayName || "Account" : undefined}
            onClick={() => setOpen((o) => !o)}
          >
            {displayName || "Account"}
          </SidebarTab>
        </div>
      </Popover.Anchor>
      <Popover.Content
        width="sm"
        align="start"
        side="top"
        sideOffset={6}
        container={typeof document !== "undefined" ? document.body : undefined}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {/* Identity header — who is signed in */}
        <div className="flex flex-col px-3 pt-2 pb-1.5">
          {user?.name && <Text font="main-content-body">{user.name}</Text>}
          <Text font="secondary-body" color="text-03">
            {user?.email ?? ""}
          </Text>
        </div>
        <PopoverMenu>
          <LineItemButton
            icon={SvgSettings}
            title="Settings"
            sizePreset="main-ui"
            variant="section"
            onClick={() => {
              setOpen(false);
              onNavigate?.();
              router.push("/app/settings");
            }}
          />
          <LineItemButton
            icon={SvgLogOut}
            title="Sign out"
            sizePreset="main-ui"
            variant="section"
            onClick={() => {
              setOpen(false);
              void logout().then(() => router.replace("/login"));
            }}
          />
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}
