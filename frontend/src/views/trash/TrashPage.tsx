"use client";

import { useState } from "react";

import { Button, Card, MessageCard, Tag, Text } from "@onyx-ai/opal/components";
import { SvgTrash } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { useRequireAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import {
  purgeTrashed,
  restoreTrashed,
  useTrash,
  type TrashEntry,
} from "@/lib/trash";

/** Display name for a trashed item: the page name (no `.md`) or folder name. */
function itemLabel(entry: TrashEntry): string {
  const base = entry.path.split("/").pop() ?? entry.path;
  return entry.kind === "page" ? base.replace(/\.md$/i, "") : base;
}

export default function TrashPage() {
  const { user, loading } = useRequireAuth();
  const { items, error: listSwrError, refresh } = useTrash();
  const [busy, setBusy] = useState<{
    id: string;
    kind: "restore" | "purge";
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const confirmDialog = useConfirm();

  if (loading || !user) return <LoadingSpinner center />;

  // Drop an item from the list once it leaves Trash (restored or purged).
  const dropFromList = (trashId: string) =>
    refresh(
      (cur) => ({
        items: (cur?.items ?? []).filter((i) => i.trash_id !== trashId),
      }),
      { revalidate: true },
    );

  const restore = async (entry: TrashEntry) => {
    setBusy({ id: entry.trash_id, kind: "restore" });
    setError(null);
    try {
      await restoreTrashed(entry.trash_id);
      await dropFromList(entry.trash_id);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? `Can't restore "${itemLabel(entry)}" — a page already exists at ${entry.path}.`
          : e instanceof Error
            ? e.message
            : "Restore failed.",
      );
    } finally {
      setBusy(null);
    }
  };

  const purge = async (entry: TrashEntry) => {
    if (
      !(await confirmDialog({
        title: `Permanently delete "${itemLabel(entry)}"?`,
        body: "It will be removed from Trash and can no longer be restored.",
        confirmLabel: "Delete permanently",
      }))
    )
      return;
    setBusy({ id: entry.trash_id, kind: "purge" });
    setError(null);
    try {
      await purgeTrashed(entry.trash_id);
      await dropFromList(entry.trash_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    } finally {
      setBusy(null);
    }
  };

  const listError = error ?? listSwrError?.message ?? null;

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgTrash}
        title="Trash"
        description="Deleted pages and folders. Restore an item to move it back to its original location, or delete it permanently. Items are auto-removed after 30 days."
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
                <div className="flex items-center gap-2">
                  <Button
                    prominence="secondary"
                    size="sm"
                    disabled={!entry.can_restore || busy?.id === entry.trash_id}
                    onClick={() => void restore(entry)}
                  >
                    {busy?.id === entry.trash_id && busy.kind === "restore"
                      ? "Restoring…"
                      : "Restore"}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={!entry.can_restore || busy?.id === entry.trash_id}
                    onClick={() => void purge(entry)}
                  >
                    {busy?.id === entry.trash_id && busy.kind === "purge"
                      ? "Deleting…"
                      : "Delete"}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
