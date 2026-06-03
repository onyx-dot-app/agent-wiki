"use client";

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

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
      <h1 className="m-0 text-[22px] leading-[1.2] font-semibold text-(--text-05)">
        {title}
      </h1>
    ) : (
      title
    );

  return (
    <header
      className="mb-6 flex flex-wrap items-center justify-between gap-4"
      style={style}
    >
      <div className="min-w-0">
        {titleNode}
        {description && (
          <p className="mt-[6px] mb-0 text-[13px] leading-[1.55] text-(--text-03)">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
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
      className="mb-3 inline-block text-[13px] text-(--text-03) no-underline"
    >
      {label}
    </Link>
  );
}
