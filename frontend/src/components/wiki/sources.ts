/** Shared source-attribution helpers for the Sources tab surfaces (panel
 * list, anchored rail, FileView wiring), a leaf module so the panel and the
 * rail never import each other. */
import { SvgFileText, SvgGlobe } from "@onyx-ai/opal/icons";
import {
  SvgBraintrust,
  SvgConfluence,
  SvgGithub,
  SvgGmail,
  SvgGoogleDrive,
  SvgHubspot,
  SvgJira,
  SvgLinear,
  SvgNotion,
  SvgSalesforce,
  SvgSharepoint,
  SvgSlack,
  SvgTeams,
  SvgZendesk,
} from "@onyx-ai/opal/logos";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import type { WriteProvenance } from "@/types";

const SOURCE_ICONS: Record<string, IconFunctionComponent> = {
  braintrust: SvgBraintrust,
  confluence: SvgConfluence,
  github: SvgGithub,
  gmail: SvgGmail,
  google_drive: SvgGoogleDrive,
  hubspot: SvgHubspot,
  jira: SvgJira,
  linear: SvgLinear,
  notion: SvgNotion,
  salesforce: SvgSalesforce,
  sharepoint: SvgSharepoint,
  slack: SvgSlack,
  teams: SvgTeams,
  web: SvgGlobe,
  zendesk: SvgZendesk,
};

export function sourceIcon(type: string | null): IconFunctionComponent {
  return (type && SOURCE_ICONS[type]) || SvgFileText;
}

// "google_drive" → "Google Drive", for the connector chip.
export function sourceTypeLabel(type: string): string {
  return type
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Stable card identity, mirroring the backend's dedupe key (document id,
 * falling back to url or title). Also joins a card to its doc spans. */
export function sourceKey(s: WriteProvenance): string {
  return s.source_document_id || s.source_url || s.source_title || "";
}
