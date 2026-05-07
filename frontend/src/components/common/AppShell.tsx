"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const NAV: NavItem[] = [
  { href: "/wiki", label: "Wiki", icon: <BookIcon /> },
  { href: "/chat", label: "Chat", icon: <ChatIcon /> },
  { href: "/triggers", label: "Triggers", icon: <BoltIcon /> },
  { href: "/events", label: "Events", icon: <EventsIcon /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 56,
          background: "#0f172a",
          color: "#e2e8f0",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "12px 0",
          gap: 4,
          flexShrink: 0,
        }}
      >
        <div ref={menuRef} style={{ position: "relative", marginBottom: 8 }}>
          <button
            onClick={() => setMenuOpen((v) => !v)}
            title={user?.email ?? "Profile"}
            aria-label="Profile menu"
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: "#6366f1",
              color: "white",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 14,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {initial}
          </button>
          {menuOpen && (
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 48,
                background: "white",
                color: "#111",
                border: "1px solid #e5e5e5",
                borderRadius: 8,
                boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
                minWidth: 200,
                padding: 8,
                zIndex: 50,
              }}
            >
              <div style={{ padding: "6px 8px", fontSize: 12, color: "#666" }}>
                {user?.email}
                {user?.is_admin && (
                  <span
                    style={{
                      marginLeft: 6,
                      padding: "1px 6px",
                      background: "#eef2ff",
                      color: "#3730a3",
                      borderRadius: 4,
                      fontSize: 10,
                      fontWeight: 600,
                    }}
                  >
                    ADMIN
                  </span>
                )}
              </div>
              <div style={{ height: 1, background: "#eee", margin: "4px 0" }} />
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
        <div style={{ height: 1, width: 28, background: "#1e293b", margin: "4px 0 8px" }} />
        {NAV.map((item) => {
          const active = pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              aria-label={item.label}
              style={{
                width: 36,
                height: 36,
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: active ? "white" : "#94a3b8",
                background: active ? "#1e293b" : "transparent",
                textDecoration: "none",
              }}
            >
              {item.icon}
            </Link>
          );
        })}
      </nav>
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
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
        borderRadius: 4,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {children}
    </button>
  );
}

function BookIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 0-3 3z" />
      <path d="M4 4v16" />
    </svg>
  );
}
function ChatIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12a8 8 0 0 1-11.5 7.2L4 21l1.8-5.5A8 8 0 1 1 21 12z" />
    </svg>
  );
}
function BoltIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
    </svg>
  );
}
function EventsIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
