"use client";

import {
  LineItemButton,
  Popover,
  PopoverMenu,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import { Content } from "@onyx-ai/opal/layouts";
import { SvgLogOut, SvgSettings, SvgUser } from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";

export interface UserMenuProps {
  folded?: boolean;
  /** Called before menu-driven navigation (e.g. to close a mobile drawer). */
  onNavigate?: () => void;
}

// Account row + anchored popover menu (name/email header, Settings,
// Sign out). Shared by AppSidebar and AdminSidebar so the profile
// affordance behaves identically in both views — clicking the row
// never navigates directly; the menu disambiguates the intent.
export default function UserMenu({
  folded = false,
  onNavigate,
}: UserMenuProps) {
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
            selected={open}
            onClick={() => setOpen((o) => !o)}
          >
            {displayName || "Account"}
          </SidebarTab>
        </div>
      </Popover.Anchor>
      <Popover.Content
        width="lg"
        align="end"
        side="right"
        sideOffset={6}
        container={typeof document !== "undefined" ? document.body : undefined}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <PopoverMenu>
          {[
            <div key="identity" className="flex flex-col p-2">
              {user?.name && <Text>{user.name}</Text>}
              <Text font="secondary-body" color="text-03">
                {user?.email ?? ""}
              </Text>
            </div>,
            null,
            <LineItemButton
              key="settings"
              icon={SvgSettings}
              title="Settings"
              sizePreset="main-ui"
              variant="section"
              onClick={() => {
                setOpen(false);
                onNavigate?.();
                router.push("/app/settings");
              }}
              rounding="sm"
            />,
            <LineItemButton
              key="sign-out"
              icon={SvgLogOut}
              title="Sign out"
              sizePreset="main-ui"
              variant="section"
              onClick={() => {
                setOpen(false);
                void logout().then(() => router.replace("/login"));
              }}
              color="danger"
              rounding="sm"
            />,
            null,
            <div key="version" className="p-2">
              <Content
                sizePreset="secondary"
                variant="body"
                color="muted"
                icon={SvgOnyxLogo}
                title={`Agent Wiki ${process.env.NEXT_PUBLIC_APP_VERSION ?? "dev"}`}
              />
            </div>,
          ]}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}
