"use client";

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { color } from "@/lib/theme";

// Single source of truth for page-level headers.
//
// - title is a string (rendered as <h1>) or a node (e.g. breadcrumbs).
// - description is muted helper text shown directly under the title.
// - actions render right-aligned and wrap below on narrow viewports.
//
// Spacing here is the standard for top-of-page chrome; don't recreate
// these values inline in route files.

export function PageHeader({
  title,
  description,
  actions,
  style,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  style?: CSSProperties;
}) {
  const titleNode =
    typeof title === "string" ? (
      <h1
        style={{
          margin: 0,
          fontSize: 22,
          fontWeight: 600,
          lineHeight: 1.2,
          color: color.text.primary,
        }}
      >
        {title}
      </h1>
    ) : (
      title
    );

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        flexWrap: "wrap",
        marginBottom: 24,
        ...style,
      }}
    >
      <div style={{ minWidth: 0 }}>
        {titleNode}
        {description && (
          <p
            style={{
              margin: "6px 0 0",
              fontSize: 13,
              lineHeight: 1.55,
              color: color.text.muted,
            }}
          >
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {actions}
        </div>
      )}
    </header>
  );
}

// Sits above a PageHeader on admin sub-pages. The 12px bottom margin
// gives a consistent gap between the back-link and the page title.
export function BackLink({
  href = "/admin",
  label = "← Admin",
}: {
  href?: string;
  label?: string;
}) {
  return (
    <Link
      href={href}
      style={{
        display: "inline-block",
        marginBottom: 12,
        fontSize: 13,
        color: color.text.muted,
        textDecoration: "none",
      }}
    >
      {label}
    </Link>
  );
}
