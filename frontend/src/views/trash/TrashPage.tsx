"use client";

import { useState } from "react";

import { Button, Card, MessageCard, Tag, Text } from "@onyx-ai/opal/components";
import { SvgTrash } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useRequireAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { restoreTrashed, useTrash, type TrashEntry } from "@/lib/trash";

/** Display name for a trashed item: the page name (no `.md`) or folder name. */
function itemLabel(entry: TrashEntry): string {
  const base = entry.path.split("/").pop() ?? entry.path;
  return entry.kind === "page" ? base.replace(/\.md$/i, "") : base;
}

export default function TrashPage() {
  const { user, loading } = useRequireAuth();
  const { items, error: listSwrError, refresh } = useTrash();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (loading || !user) return <LoadingSpinner center />;

  const restore = async (entry: TrashEntry) => {
    setBusyId(entry.trash_id);
    setError(null);
    try {
      await restoreTrashed(entry.trash_id);
      // The item is back on the tree — drop it from the list optimistically.
      await refresh(
        (cur) => ({
          items: (cur?.items ?? []).filter(
            (i) => i.trash_id !== entry.trash_id,
          ),
        }),
        { revalidate: true },
      );
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? `Can't restore "${itemLabel(entry)}" — a page already exists at ${entry.path}.`
          : e instanceof Error
            ? e.message
            : "Restore failed.",
      );
    } finally {
      setBusyId(null);
    }
  };

  const listError = error ?? listSwrError?.message ?? null;

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgTrash}
        title="Trash"
        description="Deleted pages and folders. Restore an item to move it back to its original location."
        divider
      />
      <SettingsLayouts.Body>
        {listError && (
          <div className="mb-3">
            <MessageCard variant="error" title={listError} />
          </div>
        )}

        {items.length === 0 && !listError && (
          <div className="px-2 py-4">
            <Text font="main-ui-body" color="text-03">
              Trash is empty. Deleted pages and folders show up here.
            </Text>
          </div>
        )}

        <div className="flex w-full flex-col gap-2">
          {items.map((entry) => (
            <Card
              key={entry.trash_id}
              padding="sm"
              border="solid"
              rounding="sm"
            >
              <div className="flex w-full items-center gap-3">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <div className="min-w-0 truncate">
                      <Text font="main-content-body">{itemLabel(entry)}</Text>
                    </div>
                    <Tag
                      title={entry.kind === "page" ? "Page" : "Folder"}
                      color="gray"
                    />
                  </div>
                  <div className="truncate">
                    <Text font="main-ui-body" color="text-03">
                      {`${entry.path} · deleted by ${entry.trashed_by}${
                        entry.trashed_at
                          ? ` · ${formatRelative(entry.trashed_at)}`
                          : ""
                      }`}
                    </Text>
                  </div>
                </div>
                <Button
                  prominence="secondary"
                  size="sm"
                  disabled={!entry.can_restore || busyId === entry.trash_id}
                  onClick={() => void restore(entry)}
                >
                  {busyId === entry.trash_id ? "Restoring…" : "Restore"}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
