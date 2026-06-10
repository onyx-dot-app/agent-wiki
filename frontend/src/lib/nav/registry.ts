import {
  SvgActions,
  SvgActivity,
  SvgBook,
  SvgCpu,
  SvgFiles,
  SvgGlobe,
  SvgHistory,
  SvgOnyxOctagon,
  SvgUser,
  SvgUsers,
  SvgWorkflow,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

export interface NavEntry {
  href: string;
  label: string;
  icon: IconFunctionComponent;
  description?: string;
}

export interface NavGroup {
  label: string | null;
  entries: NavEntry[];
}

export const NAV_ENTRIES = [
  {
    href: "/app/wiki",
    label: "Wiki",
    icon: SvgBook,
  },
  {
    href: "/app/triggers",
    label: "Triggers",
    icon: SvgWorkflow,
    description:
      "Watch wiki pages for specific changes, or check on recurring schedules.",
  },
  {
    href: "/app/events",
    label: "Events",
    icon: SvgActivity,
  },
  {
    href: "/app/agents",
    label: "Agents",
    icon: SvgActions,
    description: "Connect agents to read and update your wiki.",
  },
] as const satisfies NavEntry[];

export const ADMIN_NAV_GROUPS = [
  {
    label: null,
    entries: [
      { href: "/admin/language-models", label: "Language Models (LLM)", icon: SvgCpu },
      { href: "/admin/web", label: "Web Search", icon: SvgGlobe },
    ],
  },
  {
    label: "Documents",
    entries: [
      { href: "/admin/templates", label: "Wiki Templates", icon: SvgFiles },
      { href: "/admin/ingest", label: "Onyx Integration", icon: SvgOnyxOctagon },
    ],
  },
  {
    label: "Permissions",
    entries: [
      { href: "/admin/users", label: "Users", icon: SvgUser },
      { href: "/admin/groups", label: "Groups", icon: SvgUsers },
    ],
  },
  {
    label: "Statistics",
    entries: [
      { href: "/admin/health", label: "Status", icon: SvgActivity },
      { href: "/admin/braintrust", label: "Usage Tracking", icon: SvgHistory },
    ],
  },
] satisfies NavGroup[];
