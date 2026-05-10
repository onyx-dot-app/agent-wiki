"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { WikiSearch, type WikiSearchHandle } from "@/components/wiki/WikiSearch";
import { useAuth } from "@/lib/auth";
import { useHealth } from "@/lib/health";
import { useLLMStatus } from "@/lib/llm";
import { color, radius, shadow } from "@/lib/theme";
import { MOBILE_BREAKPOINT, useIsMobile } from "@/lib/viewport";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const NAV: NavItem[] = [
  { href: "/wiki", label: "Wiki", icon: <BookIcon /> },
  { href: "/triggers", label: "Triggers", icon: <BoltIcon /> },
  { href: "/events", label: "Events", icon: <EventsIcon /> },
  { href: "/agents", label: "Agents", icon: <AgentsIcon /> },
];

const BANNER_HEALTH_POLL_MS = 15000;
const SIDEBAR_WIDTH = 248;
// Tight icon column. 28px avatar + 10px symmetric breathing room = 48.
// Items are optically centered via per-element padding (see profile
// button + nav link + search button styles below).
const SIDEBAR_COLLAPSED_WIDTH = 48;
const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);
  // AppShell only mounts client-side (each page gates on useRequireAuth
  // before rendering us), so reading localStorage in the lazy initializer
  // is safe — and avoids the expanded→collapsed animation flicker on every
  // route change that an effect-based hydration would cause.
  //
  // On mobile, default to collapsed unless the user has an explicit
  // stored preference. (Desktop users with no preference default to
  // expanded — preserves the existing behavior.)
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const stored = window.localStorage.getItem(COLLAPSED_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    return window.innerWidth < MOBILE_BREAKPOINT;
  });
  // Visibility of the hover-rail toggle. `navHover` covers cursor inside
  // the sidebar; `railHover` covers the toggle itself, which protrudes
  // past the sidebar's right edge.
  const [navHover, setNavHover] = useState(false);
  const [railHover, setRailHover] = useState(false);
  const showRail = navHover || railHover;
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<WikiSearchHandle>(null);

  // Mobile: when sidebar is expanded it floats over content as an
  // overlay drawer. Tapping a nav link (or the backdrop) closes it.
  const isMobileDrawer = isMobile && !collapsed;

  function expandAndFocusSearch() {
    setCollapsed(false);
    // Wait for WikiSearch to mount on the next render before focusing.
    // setTimeout(0) yields to React's commit phase; the imperative
    // handle is set during commit, so the focus call lands on the
    // mounted input.
    setTimeout(() => searchRef.current?.focus(), 0);
  }

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    if (!menuOpen) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", handleClick);
    return () => window.removeEventListener("mousedown", handleClick);
  }, [menuOpen]);

  const initial = (user?.name || user?.email || "?").charAt(0).toUpperCase();
  const displayName = user?.name || user?.email || "";

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "white" }}>
      {/* Mobile drawer backdrop. Only rendered when the sidebar is
          expanded on a phone — tapping it closes the drawer. */}
      {isMobileDrawer && (
        <div
          onClick={() => setCollapsed(true)}
          aria-hidden
          style={{
            position: "fixed",
            inset: 0,
            background: color.overlay,
            zIndex: 50,
          }}
        />
      )}
      <nav
        onMouseEnter={() => setNavHover(true)}
        onMouseLeave={() => setNavHover(false)}
        style={{
          width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_WIDTH,
          background: color.bg.panel,
          // Right edge drawn as an inset box-shadow rather than a
          // border-right. A 1px asymmetric border would shift the
          // optical center of the sidebar by 0.5px and consume layout
          // width — fine in a 248px expanded bar, very visible in a
          // 48px collapsed bar where icons need to sit dead-center.
          boxShadow: isMobileDrawer
            ? `inset -1px 0 0 0 ${color.border.default}, ${shadow.panel}`
            : `inset -1px 0 0 0 ${color.border.default}`,
          color: color.text.primary,
          display: "flex",
          flexDirection: "column",
          // border-box so `width: 48` is the *visible* width, not the
          // content area. Without this the project's default
          // content-box would render the sidebar at 48 + 8 (padding)
          // + 1 (border) = 57px wide.
          boxSizing: "border-box",
          // Side padding 4 + 28px avatar + 4px on the right = 36
          // content; the leftover 12px of the 48 visible bar splits
          // around the avatar's button padding to keep it centered.
          padding: "10px 4px",
          gap: 2,
          flexShrink: 0,
          // Mobile expanded state: float as a fixed overlay so the
          // 248px drawer doesn't push the page content off-screen on
          // a 375px phone. Desktop / collapsed: in-flow.
          position: isMobileDrawer ? "fixed" : "relative",
          ...(isMobileDrawer
            ? { top: 0, left: 0, height: "100vh", zIndex: 60 }
            : {}),
          transition: "width 160ms ease",
          // Intentionally NOT overflow: hidden — would clip the profile
          // dropdown menu and the search results popover, both of which
          // legitimately extend past the sidebar's right edge.
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            // No collapse-conditional alignment. Children stay anchored
            // to flex-start in both states so the avatar's x-coordinate
            // doesn't shift during the width transition.
            marginBottom: 6,
          }}
        >
          <div
            ref={menuRef}
            // Always flex:1 so the menuRef left edge sits at the nav's
            // content-left anchor (x=8) regardless of state.
            style={{ position: "relative", flex: 1, minWidth: 0 }}
          >
            <button
              onClick={() => setMenuOpen((v) => !v)}
              title={user?.email ?? "Profile"}
              aria-label="Profile menu"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                width: "100%",
                minWidth: 0,
                // Collapsed: button-padding-left 6 + nav-padding-left 4
                // = avatar at x=10. Avatar-center at x=24, equal to
                // sidebar-center (48/2). Optically centered.
                // Expanded: padding-left 8 leaves room for the name.
                padding: collapsed ? "4px 6px" : "4px 8px",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                borderRadius: radius.sm,
                color: color.text.primary,
                textAlign: "left",
                transition: "padding 160ms ease",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = color.bg.hover)}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <span
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: radius.sm,
                  background: color.accent.bg,
                  color: color.accent.fg,
                  fontWeight: 600,
                  fontSize: 12,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                {initial}
              </span>
              {!collapsed && (
                <>
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      fontSize: 13,
                      fontWeight: 500,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {displayName}
                  </span>
                  <span style={{ color: color.text.muted, display: "flex", flexShrink: 0 }}>
                    <ChevronDown />
                  </span>
                </>
              )}
            </button>
            {menuOpen && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  left: 0,
                  background: color.bg.page,
                  color: color.text.primary,
                  border: `1px solid ${color.border.default}`,
                  borderRadius: radius.md,
                  boxShadow: shadow.md,
                  minWidth: 220,
                  // Cap so the menu can't overflow the right edge of the
                  // viewport on narrow phones.
                  maxWidth: "calc(100vw - 24px)",
                  padding: 6,
                  zIndex: 70,
                }}
              >
                <div style={{ padding: "6px 8px", fontSize: 12, color: color.text.muted }}>
                  {user?.email}
                  {user?.is_admin && (
                    <span
                      style={{
                        marginLeft: 6,
                        padding: "1px 6px",
                        background: color.bg.active,
                        color: color.text.primary,
                        borderRadius: radius.xs,
                        fontSize: 10,
                        fontWeight: 600,
                        letterSpacing: 0.4,
                      }}
                    >
                      ADMIN
                    </span>
                  )}
                </div>
                <div style={{ height: 1, background: color.border.subtle, margin: "4px 0" }} />
                {user?.is_admin && (
                  <MenuButton
                    onClick={() => {
                      setMenuOpen(false);
                      router.push("/admin");
                    }}
                  >
                    Admin
                  </MenuButton>
                )}
                <MenuButton
                  onClick={async () => {
                    setMenuOpen(false);
                    await logout();
                    router.replace("/login");
                  }}
                >
                  Sign out
                </MenuButton>
              </div>
            )}
          </div>
          {/* Collapse/expand handled by the hover-rail toggle (below). */}
        </div>

        {collapsed ? (
          // Search affordance in the icon column. Click expands the
          // sidebar and focuses the freshly-mounted search input —
          // discoverability + one-click search from collapsed state.
          <button
            onClick={expandAndFocusSearch}
            title="Search"
            aria-label="Search wiki"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              width: "100%",
              height: 30,
              // padding-left 11: icon at x=4 (nav) + 11 (button) = 15;
              // icon-center at x=24 = sidebar-center.
              padding: "0 11px",
              marginBottom: 6,
              background: "transparent",
              border: "none",
              cursor: "pointer",
              borderRadius: radius.sm,
              color: color.text.muted,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = color.bg.hover;
              e.currentTarget.style.color = color.text.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = color.text.muted;
            }}
          >
            <span
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 18,
                height: 18,
                flexShrink: 0,
              }}
            >
              <SearchGlyph />
            </span>
          </button>
        ) : (
          <div style={{ padding: "0 0 6px" }}>
            <WikiSearch ref={searchRef} />
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {NAV.map((item) => {
            const active = pathname?.startsWith(item.href) ?? false;
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                aria-label={item.label}
                onClick={() => {
                  // Mobile drawer: nav navigation closes the overlay
                  // so the user lands on the page content, not still
                  // looking at a half-screen drawer.
                  if (isMobileDrawer) setCollapsed(true);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  height: 30,
                  // Collapsed: link-padding-left 11 + nav-padding-left 4
                  // = icon at x=15. Icon-center at x=24 = sidebar-center.
                  // Expanded: padding-left 8 aligns with the profile
                  // button label spacing.
                  padding: collapsed ? "0 11px" : "0 8px",
                  borderRadius: radius.sm,
                  color: active ? color.text.primary : color.text.muted,
                  background: active ? color.bg.active : "transparent",
                  textDecoration: "none",
                  fontSize: 14,
                  fontWeight: active ? 500 : 400,
                  transition: "padding 160ms ease, background 80ms ease, color 80ms ease",
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.background = color.bg.hover;
                    e.currentTarget.style.color = color.text.primary;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.color = color.text.muted;
                  }
                }}
              >
                <span
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 18,
                    height: 18,
                    color: "currentColor",
                    flexShrink: 0,
                  }}
                >
                  {item.icon}
                </span>
                {!collapsed && (
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.label}
                  </span>
                )}
              </Link>
            );
          })}
        </div>

        {/* Hover-rail collapse/expand toggle. Hidden on mobile — touch
            devices don't hover, and the drawer pattern uses backdrop-tap
            to close. The collapsed-state search button is the entry
            point to expand on mobile. */}
        {!isMobile && (
        <button
          onMouseEnter={(e) => {
            setRailHover(true);
            e.currentTarget.style.color = color.text.primary;
          }}
          onMouseLeave={(e) => {
            setRailHover(false);
            e.currentTarget.style.color = color.text.muted;
          }}
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          style={{
            position: "absolute",
            // Vertically centered on the sidebar (which is full
            // viewport height), so the toggle sits at mid-screen
            // regardless of how tall the nav list grows.
            top: "50%",
            right: -10,
            width: 20,
            height: 20,
            borderRadius: "50%",
            background: color.bg.page,
            border: `1px solid ${color.border.default}`,
            boxShadow: shadow.sm,
            color: color.text.muted,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            padding: 0,
            opacity: showRail ? 1 : 0,
            // Combined transform: vertical centering (-50%) plus a
            // horizontal slide-in nudge driven by hover state.
            transform: showRail
              ? "translate(0, -50%)"
              : "translate(-4px, -50%)",
            transition: "opacity 120ms ease, transform 120ms ease, color 80ms ease",
            zIndex: 30,
          }}
        >
          {collapsed ? <ChevronRight /> : <ChevronLeft />}
        </button>
        )}
      </nav>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <StatusBanner />
        <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
      </div>
    </div>
  );
}

// Renders at most one banner at a time. Backend health takes precedence
// over the LLM setup banner — if both signals fire, the user sees the
// health banner only.
function StatusBanner() {
  const { user, loading } = useAuth();
  const skip = loading || !user;
  const { health, error: healthError } = useHealth({
    refreshIntervalMs: skip ? undefined : BANNER_HEALTH_POLL_MS,
  });
  const { status: llmStatus } = useLLMStatus({ skip });

  const backendUnreachable = !skip && !!healthError;
  const backendDegraded = !skip && health?.status === "degraded";

  if (skip) return null;
  if (backendUnreachable || backendDegraded) {
    return (
      <BackendHealthBanner
        unreachable={backendUnreachable}
        isAdmin={!!user?.is_admin}
        message={healthError?.message ?? null}
      />
    );
  }
  if (llmStatus?.configured === false) {
    return <LLMSetupBanner isAdmin={!!user?.is_admin} />;
  }
  return null;
}

function BannerShell({
  tone,
  children,
}: {
  tone: "warning" | "error";
  children: ReactNode;
}) {
  const palette = tone === "error" ? color.state.danger : color.state.warning;
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 16px",
        background: palette.bg,
        borderBottom: `1px solid ${palette.border}`,
        color: palette.fg,
        fontSize: 14,
      }}
    >
      <span aria-hidden style={{ fontSize: 16, lineHeight: 1 }}>⚠️</span>
      <span style={{ flex: 1 }}>{children}</span>
    </div>
  );
}

function BackendHealthBanner({
  unreachable,
  isAdmin,
  message,
}: {
  unreachable: boolean;
  isAdmin: boolean;
  message: string | null;
}) {
  // Not dismissible — the banner is driven by live polling and will
  // disappear automatically once the backend recovers.
  return (
    <BannerShell tone="error">
      <strong>{unreachable ? "Backend unreachable." : "Backend degraded."}</strong>{" "}
      {unreachable
        ? "The frontend can't reach the backend. Some features will not work until it recovers."
        : "One or more background queues are reporting errors."}{" "}
      {isAdmin ? (
        <Link
          href="/admin/health"
          style={{ color: "inherit", textDecoration: "underline", fontWeight: 600 }}
        >
          View health details
        </Link>
      ) : (
        <span>Please ask a workspace admin to investigate.</span>
      )}
      {message && isAdmin && (
        <span style={{ marginLeft: 8, fontSize: 12, opacity: 0.8 }}>({message})</span>
      )}
    </BannerShell>
  );
}

function LLMSetupBanner({ isAdmin }: { isAdmin: boolean }) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && sessionStorage.getItem("llm-banner-dismissed") === "1") {
      setDismissed(true);
    }
  }, []);

  if (dismissed) return null;

  return (
    <BannerShell tone="warning">
      <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ flex: 1 }}>
          <strong>No language model is configured.</strong>{" "}
          {isAdmin ? (
            <>
              AI features (chat, document updates, trigger evaluation) are disabled until you add a
              provider and API key on the{" "}
              <Link
                href="/admin/llm"
                style={{ color: color.state.warning.fg, textDecoration: "underline", fontWeight: 600 }}
              >
                LLM settings page
              </Link>
              .
            </>
          ) : (
            <>
              AI features (chat, document updates, trigger evaluation) are disabled. Please ask a
              workspace admin to finish setup.
            </>
          )}
        </span>
        <button
          onClick={() => {
            if (typeof window !== "undefined") {
              sessionStorage.setItem("llm-banner-dismissed", "1");
            }
            setDismissed(true);
          }}
          aria-label="Dismiss"
          style={{
            background: "transparent",
            border: "none",
            color: color.state.warning.fg,
            cursor: "pointer",
            fontSize: 18,
            lineHeight: 1,
            padding: "2px 6px",
            borderRadius: radius.xs,
          }}
        >
          ×
        </button>
      </span>
    </BannerShell>
  );
}

function MenuButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "8px 10px",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        fontSize: 14,
        borderRadius: radius.xs,
        color: color.text.primary,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = color.bg.hover)}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {children}
    </button>
  );
}

function ChevronDown() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function SearchGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

function ChevronLeft() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H3z" />
      <path d="M21 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7z" />
    </svg>
  );
}
function BoltIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
    </svg>
  );
}
function EventsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="2" />
      <path d="M16.24 7.76a6 6 0 0 1 0 8.49" />
      <path d="M7.76 16.24a6 6 0 0 1 0-8.49" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
      <path d="M4.93 19.07a10 10 0 0 1 0-14.14" />
    </svg>
  );
}
function AgentsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {/* twin angled antennae */}
      <path d="M8 3l-1 3" />
      <path d="M16 3l1 3" />
      {/* head */}
      <rect x="4" y="6" width="16" height="13" rx="3" />
      {/* side bolts */}
      <path d="M2 11v3" />
      <path d="M22 11v3" />
      {/* eyes (outline, no fill) */}
      <circle cx="9" cy="12" r="1.3" />
      <circle cx="15" cy="12" r="1.3" />
      {/* mouth */}
      <path d="M9 16h6" />
    </svg>
  );
}
