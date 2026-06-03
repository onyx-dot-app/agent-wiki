import {
  SvgActions,
  SvgActivity,
  SvgDocFile,
  SvgDownloadCloud,
  SvgGlobe,
  SvgKey,
  SvgLinkedDots,
  SvgOrganization,
  SvgUsers,
  SvgWorkflow,
  SvgBook,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

export interface NavEntry {
  href: string;
  label: string;
  icon: IconFunctionComponent;
  description?: string;
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

export const ADMIN_NAV_ENTRIES = [
  { href: "/admin/language-models", label: "Language Models", icon: SvgKey },
  { href: "/admin/web", label: "Web Search", icon: SvgGlobe },
  { href: "/admin/users", label: "Users", icon: SvgUsers },
  { href: "/admin/groups", label: "Groups", icon: SvgOrganization },
  { href: "/admin/health", label: "Health", icon: SvgActivity },
  { href: "/admin/braintrust", label: "Braintrust", icon: SvgLinkedDots },
  { href: "/admin/templates", label: "Templates", icon: SvgDocFile },
  { href: "/admin/ingest", label: "Onyx Connection", icon: SvgDownloadCloud },
] as const satisfies NavEntry[];
