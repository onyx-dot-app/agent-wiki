import { SelectCard, Tag, Text } from "@onyx-ai/opal/components";

import { color } from "@/lib/theme";
import { relativeTime } from "@/lib/time";
import { type CommitInfo, parseCommitSource } from "@/lib/wiki";

export interface HistoryPanelProps {
  commits: CommitInfo[] | null;
  error: string | null;
  headSha: string | null;
  viewingSha: string | null;
  onPick: (sha: string) => void;
  onPickLatest: () => void;
  onClose: () => void;
  /** When true (mobile sheet mode), fill the entire host container
   *  edge-to-edge instead of rendering as a fixed-width rounded card. */
  fullHeight?: boolean;
}

/**
 * Activity / version history side panel for a wiki page. Mirrors the
 * Onyx Wiki history-feed mock: each entry is a selectable card showing
 * the author, a relative timestamp, and a short sha, with the working
 * tree pinned to the top as "Current Version".
 */
export function HistoryPanel({
  commits,
  error,
  headSha,
  viewingSha,
  onPick,
  onPickLatest,
  onClose,
  fullHeight = false,
}: HistoryPanelProps) {
  const latestActive = viewingSha === null;
  return (
    <aside
      style={{
        width: fullHeight ? "100%" : 400,
        height: fullHeight ? "100%" : undefined,
        flexShrink: 0,
        background: color.bg.panel,
        borderRadius: fullHeight ? 0 : 12,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        padding: 8,
        gap: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 4,
          padding: 4,
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <Text font="main-ui-action" color="text-04">
            History
          </Text>
        </div>
        <button
          onClick={onClose}
          aria-label="Close history"
          style={{
            appearance: "none",
            background: "transparent",
            border: "none",
            color: color.text.muted,
            cursor: "pointer",
            fontSize: 18,
            lineHeight: 1,
            padding: 4,
            borderRadius: 4,
            flexShrink: 0,
          }}
        >
          ×
        </button>
      </div>
      <div
        style={{
          overflowY: "auto",
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {error && (
          <div style={{ padding: 12 }}>
            <Text font="secondary-body" color="text-03">
              {error}
            </Text>
          </div>
        )}
        {!error && commits === null && (
          <div style={{ padding: 12 }}>
            <Text font="secondary-body" color="text-03">
              Loading…
            </Text>
          </div>
        )}
        {!error && commits && commits.length === 0 && (
          <div style={{ padding: 12 }}>
            <Text font="secondary-body" color="text-03">
              No history yet.
            </Text>
          </div>
        )}
        {!error && commits && commits.length > 0 && (
          <>
            <ActivityRow
              active={latestActive}
              isLatest
              onClick={onPickLatest}
              title="Latest (working tree)"
              author=""
              ts=""
              description={
                headSha ? `Current HEAD · ${headSha.slice(0, 7)}` : "—"
              }
            />
            {commits.map((c) => {
              const { url, title: srcTitle } = parseCommitSource(c.body);
              return (
                <ActivityRow
                  key={c.sha}
                  active={!latestActive && viewingSha === c.sha}
                  isLatest={false}
                  onClick={() => onPick(c.sha)}
                  title={c.author || "Unknown"}
                  author={c.author}
                  ts={relativeTime(c.ts, "long")}
                  description={c.sha.slice(0, 7)}
                  sourceUrl={url}
                  sourceTitle={srcTitle}
                />
              );
            })}
          </>
        )}
      </div>
    </aside>
  );
}

interface ActivityRowProps {
  active: boolean;
  isLatest: boolean;
  onClick: () => void;
  title: string;
  author: string;
  ts: string;
  description: string;
  sourceUrl?: string | null;
  sourceTitle?: string | null;
}

function ActivityRow({
  active,
  isLatest,
  onClick,
  title,
  author,
  ts,
  description,
  sourceUrl,
  sourceTitle,
}: ActivityRowProps) {
  const initial = (author || title || "?").charAt(0).toUpperCase();
  return (
    <SelectCard
      state={active ? "selected" : "empty"}
      onClick={onClick}
      padding="xs"
      rounding="md"
      border="none"
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
          width: "100%",
          minWidth: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            gap: 8,
            width: "100%",
            minWidth: 0,
          }}
        >
          <Avatar initial={initial} />
          <div
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
              {title}
            </Text>
          </div>
          {isLatest ? (
            <Tag color="blue" size="sm" title="Current Version" />
          ) : (
            <div style={{ flexShrink: 0 }}>
              <Text font="secondary-body" color="text-03" nowrap>
                {ts}
              </Text>
            </div>
          )}
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            gap: 4,
            width: "100%",
            minWidth: 0,
            paddingLeft: 28,
          }}
        >
          <div
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
              {description}
            </Text>
          </div>
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              aria-label={sourceTitle ?? "Open source"}
              style={{
                display: "inline-flex",
                alignItems: "center",
                padding: 2,
                borderRadius: 4,
                color: "inherit",
                textDecoration: "none",
                flexShrink: 0,
              }}
            >
              <Text font="secondary-body" color="text-03">
                ↗
              </Text>
            </a>
          ) : null}
        </div>
        {sourceTitle && !sourceUrl ? (
          <div style={{ paddingLeft: 28, width: "100%", minWidth: 0 }}>
            <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
              {sourceTitle}
            </Text>
          </div>
        ) : null}
      </div>
    </SelectCard>
  );
}

/** Single-initial avatar. Inverts per theme via the --diff-avatar-*
 *  CSS vars defined in globals.css. */
function Avatar({ initial }: { initial: string }) {
  return (
    <div
      aria-hidden
      style={{
        width: 20,
        height: 20,
        borderRadius: 9999,
        background: "var(--diff-avatar-bg, #000000)",
        color: "var(--diff-avatar-fg, #ffffff)",
        border: "1px solid var(--diff-avatar-border, #e6e6e6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {initial}
    </div>
  );
}
