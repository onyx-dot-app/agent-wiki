"use client";

import { useState } from "react";
import { Button, Divider, Tag, Text } from "@onyx-ai/opal/components";
import { SvgEmpty, SvgNotFound } from "@onyx-ai/opal/illustrations";
import {
  SvgActivity,
  SvgChevronDown,
  SvgChevronUp,
  SvgSparkle,
  SvgWorkflow,
  SvgX,
} from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";
import { Content, IllustrationContent, Section } from "@onyx-ai/opal/layouts";
import { timeAgo } from "@onyx-ai/opal/time";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PanelSearchField } from "@/components/wiki/PanelSearch";
import { AvatarCluster, ScopeChip } from "@/components/triggers/fireParts";
import { useActivityFeed, type FeedItem } from "@/lib/activities/feed";
import type { AppEvent } from "@/lib/activities";
import {
  dismissNotification,
  notifLink,
  type NotificationView,
} from "@/lib/notifications";
import { useAuth } from "@/lib/auth";
import { useLeftPanel } from "@/providers/LeftPanelProvider";

interface ActivityPayload {
  doc_path?: string;
  change_kind?: string;
  reason?: string;
  message?: string;
  destination_type?: string;
  destination_name?: string | null;
  count?: number;
  threshold?: number;
  cap?: number;
  // automanage.applied (Auto Organize auto-applied cleanup)
  op?: string;
  source_paths?: string[];
  target_paths?: string[];
  applied_sha?: string;
}

interface RowTexts {
  /** Page/folder path for the leading scope tag. */
  chipScope: string;
  /** Collapsed one-liner: plain prefix + bold subject. */
  prefix: string;
  subject: string;
  /** Expanded paragraph, null when the row has no detail to expand. */
  body: string | null;
  destinationTypes: string[];
}

/** Auto Organize events are acted by the AI system user, not the viewer. */
function isAutoOrganizeEvent(event: AppEvent): boolean {
  return event.kind.startsWith("automanage.");
}

/** Chip location for an event. Auto Organize events point the chip at the
 * *parent* folder: the acted-on path itself no longer exists (it was cleaned
 * up), and the row's subject already names the item. */
function eventScope(event: AppEvent): string {
  const p = event.payload as ActivityPayload;
  if (isAutoOrganizeEvent(event)) {
    const path = p.source_paths?.[0] ?? event.target ?? "";
    return path.split("/").slice(0, -1).join("/");
  }
  return p.doc_path ?? event.target ?? "";
}

function destinationName(type: string | undefined): string {
  if (type === "slack") return "Slack";
  if (type === "email") return "Email";
  return "Activity Center";
}

function eventTexts(event: AppEvent): RowTexts {
  const p = event.payload as ActivityPayload;
  const chipScope = eventScope(event);
  if (event.kind === "wiki.frequent_updates") {
    return {
      chipScope,
      prefix: "Frequent auto-updates on",
      subject: p.doc_path ?? "a page",
      body: `Auto-updated ${p.count ?? "?"} times in the past 24 hours${
        p.threshold ? ` (warns at ${p.threshold})` : ""
      }.`,
      destinationTypes: [],
    };
  }
  if (event.kind === "wiki.auto_update_capped") {
    return {
      chipScope,
      prefix: "Auto-updates paused on",
      subject: p.doc_path ?? "a page",
      body: `Hit the limit of ${p.cap ?? "?"} auto-updates in 24 hours, so further auto-updates are paused for now.`,
      destinationTypes: [],
    };
  }
  if (event.kind === "trigger.fire") {
    const verb =
      p.destination_type === "event_log" || !p.destination_type
        ? "Sent a notification to"
        : "Sent a message to";
    return {
      chipScope,
      prefix: verb,
      subject: p.destination_name ?? destinationName(p.destination_type),
      body: p.message || p.reason || null,
      destinationTypes: [p.destination_type ?? "event_log"],
    };
  }
  if (event.kind === "automanage.applied") {
    // One glanceable line: the chip shows where, the Wiki AI avatar shows who,
    // and the subject is just the item's name (a full path would render the
    // chip's path twice and wrap). No body — deletes are restorable from
    // Trash like any other delete.
    const path = p.source_paths?.[0] ?? event.target ?? "";
    const name = path.split("/").pop() || "a page";
    return {
      chipScope,
      prefix:
        p.op === "delete_empty_folder"
          ? "Removed empty folder"
          : "Auto-organized",
      subject: name,
      body: null,
      destinationTypes: [],
    };
  }
  // Unknown kinds stay legible instead of masquerading as trigger fires,
  // and their payload stays inspectable when it has no message/reason.
  const payloadKeys = Object.keys(event.payload ?? {});
  return {
    chipScope,
    prefix: "Event",
    subject: event.kind,
    body:
      p.message ||
      p.reason ||
      (payloadKeys.length ? JSON.stringify(event.payload) : null),
    destinationTypes: [],
  };
}

function notificationTexts(n: NotificationView): RowTexts {
  // A notification's deep link is its scope when it points at a wiki page.
  const link = notifLink(n);
  const chipScope = link?.startsWith("/app/wiki/")
    ? decodeURIComponent(link.slice("/app/wiki/".length))
    : "";
  return {
    chipScope,
    prefix: "",
    subject: n.title,
    body: n.description,
    destinationTypes: ["event_log"],
  };
}

function itemTexts(item: FeedItem): RowTexts {
  return item.kind === "event"
    ? eventTexts(item.event)
    : notificationTexts(item.notification);
}

// Searchable text for a feed row regardless of kind.
function itemHaystack(item: FeedItem): string {
  const t = itemTexts(item);
  return [t.chipScope, t.prefix, t.subject, t.body]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/** Unread marker (mock Badge): 6px filled dot unread, 8px hollow ring read. */
function UnreadBadge({ unread }: { unread: boolean }) {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      {unread ? (
        <span className="size-[6px] rounded-full bg-(--action-link-05)" />
      ) : (
        <span className="size-2 rounded-full border border-(--border-02)" />
      )}
    </span>
  );
}

/** One feed row (mock Activity 37:131140 expanded / 37:133188 collapsed):
 *  scope tag + workflow glyph + avatars + time + badge over a body line
 *  that expands from a bold one-liner to the full detail paragraph. */
function FeedRow({ item, ownerName }: { item: FeedItem; ownerName: string }) {
  const { chipScope, prefix, subject, body, destinationTypes } =
    itemTexts(item);
  const [override, setOverride] = useState<boolean | null>(null);
  // Unread rows open expanded (the mock's "New" section shows details),
  // read ones collapsed. The chevron overrides.
  const open = override ?? (item.unread && body !== null);

  const toggle = () => {
    setOverride(!open);
    // Expanding an unread notification is reading it: the server flips
    // dismissed, and the row drifts to "Older" on the next revalidation
    // rather than jumping mid-read.
    if (item.kind === "notification" && !item.notification.dismissed && !open) {
      void dismissNotification(item.notification.id);
    }
  };

  const oneLiner = prefix ? `${prefix} **${subject}**` : `**${subject}**`;

  return (
    <div className="flex w-full shrink-0 flex-col rounded-(--radius-08) p-1">
      <div className="flex w-full items-center p-[2px]">
        <div className="flex min-w-0 flex-1 items-center gap-1 p-[2px]">
          <ScopeChip scope={chipScope} />
          <span className="flex size-4 shrink-0 items-center justify-center p-[2px]">
            {item.kind === "event" && item.event.kind === "trigger.fire" ? (
              <SvgWorkflow className="size-3 text-(--text-02)" />
            ) : item.kind === "event" && isAutoOrganizeEvent(item.event) ? (
              <SvgSparkle className="size-3 text-(--text-02)" />
            ) : (
              <SvgActivity className="size-3 text-(--text-02)" />
            )}
          </span>
          <AvatarCluster
            ownerName={
              item.kind === "event" && isAutoOrganizeEvent(item.event)
                ? "Wiki AI"
                : ownerName
            }
            destinationTypes={destinationTypes}
          />
        </div>
        <div className="flex shrink-0 items-center gap-1 p-[2px]">
          <Text font="secondary-body" color="text-03" nowrap>
            {timeAgo(item.iso) ?? ""}
          </Text>
          <UnreadBadge unread={item.unread} />
        </div>
      </div>
      <div className="flex w-full items-start gap-[2px] p-1 pt-0">
        {open && body ? (
          <>
            <div className="min-w-0 flex-1 px-[2px]">
              <Text font="main-ui-body" color="text-03">
                {markdown(body)}
              </Text>
            </div>
            <Button
              icon={SvgChevronUp}
              size="xs"
              prominence="tertiary"
              tooltip="Details"
              onClick={toggle}
            />
          </>
        ) : (
          <>
            <div className="flex min-w-0 flex-1 items-center py-[2px]">
              <span className="min-w-0 px-[2px]">
                <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
                  {markdown(oneLiner)}
                </Text>
              </span>
            </div>
            {body && (
              <Button
                icon={SvgChevronDown}
                size="xs"
                prominence="tertiary"
                tooltip="Details"
                onClick={toggle}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Left-side Activities panel (mock 37:131078): the single feed for app
 *  events and per-user notifications now that the header bell is gone. */
export default function ActivitiesPanel() {
  const { toggleActivities } = useLeftPanel();
  const { user } = useAuth();
  const ownerName = user?.name || user?.email || "?";
  const [query, setQuery] = useState("");
  const feed = useActivityFeed();

  const filtered = query
    ? feed.items.filter((i) => itemHaystack(i).includes(query.toLowerCase()))
    : feed.items;
  const fresh = filtered.filter((i) => i.unread);
  const older = filtered.filter((i) => !i.unread);

  return (
    <div className="left-panel-card gap-1">
      {/* Title Line (36px): title, unread tag, close. */}
      <div className="flex shrink-0 items-start gap-1">
        <div className="min-w-0 flex-1 p-2 text-(--text-04)">
          <Content
            sizePreset="main-ui"
            variant="section"
            icon={SvgActivity}
            title="Activity History"
          />
        </div>
        {feed.unreadCount > 0 && (
          <span className="flex shrink-0 items-center px-1 py-[6px]">
            <Tag title={`${feed.unreadCount} new`} color="blue" size="md" />
          </span>
        )}
        <Button
          icon={SvgX}
          prominence="tertiary"
          tooltip="Close Panel"
          onClick={toggleActivities}
        />
      </div>

      {/* Actions Bar (36px): search plus the mock's reserved action slot. */}
      <div className="flex shrink-0 items-center gap-1">
        <PanelSearchField
          value={query}
          onChange={setQuery}
          placeholder="Search activity history…"
        />
        <span aria-hidden className="size-9 shrink-0" />
      </div>

      {/* Feed: no visible scrollbar, bottom fade from the mock's Mask. */}
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0}
        className="scroll-fade-bottom scroll-y-hidden min-h-0 flex-1 overflow-y-auto"
      >
        {feed.isLoading && <LoadingSpinner center />}

        {!feed.isLoading && feed.items.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <IllustrationContent
              illustration={SvgEmpty}
              title="No activity yet."
            />
          </div>
        )}

        {!feed.isLoading && feed.items.length > 0 && filtered.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <IllustrationContent
              illustration={SvgNotFound}
              title="No matching activity found."
            />
          </div>
        )}

        {fresh.length > 0 && (
          <>
            <div className="pt-1">
              <Divider title="New" />
            </div>
            {fresh.map((item) => (
              <FeedRow key={item.key} item={item} ownerName={ownerName} />
            ))}
          </>
        )}

        {older.length > 0 && (
          <>
            <div className="pt-1">
              <Divider title="Older" />
            </div>
            {older.map((item) => (
              <FeedRow key={item.key} item={item} ownerName={ownerName} />
            ))}
          </>
        )}
      </Section>
    </div>
  );
}
