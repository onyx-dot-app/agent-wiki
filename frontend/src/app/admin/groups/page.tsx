"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useSWRConfig } from "swr";

import { Button, Card, InputTypeIn, Text } from "@onyx-ai/opal/components";
import { ContentAction, IllustrationContent } from "@onyx-ai/opal/layouts";
import { SvgChevronRight, SvgPlusCircle, SvgUsers } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { SvgNoResult } from "@onyx-ai/opal/illustrations";

import { RequireAdmin } from "@/components/RequireAdmin";
import { renameGroup, useGroups, type Group } from "@/lib/permissions";

import styles from "./groups.module.css";

function groupSub(g: Group): string {
  const parts: string[] = [];
  if (g.folder_count > 0) {
    parts.push(`${g.folder_count} ${g.folder_count === 1 ? "folder" : "folders"}`);
  }
  if (g.page_count > 0) {
    parts.push(`${g.page_count} wiki ${g.page_count === 1 ? "page" : "pages"}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "No private pages";
}

export default function AdminGroupsPage() {
  return (
    <RequireAdmin>
      <SettingsLayouts.Root width="md">
        <SettingsLayouts.Header icon={SvgUsers} title="Groups" backButton />
        <SettingsLayouts.Body>
          <GroupsList />
        </SettingsLayouts.Body>
      </SettingsLayouts.Root>
    </RequireAdmin>
  );
}

function GroupsList() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const { groups, error, isLoading } = useGroups();
  const [query, setQuery] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups.filter((g) => !q || g.name.toLowerCase().includes(q));
  }, [groups, query]);

  async function handleRename(id: string, newName: string) {
    const trimmed = newName.trim();
    if (!trimmed) return;
    setRenameError(null);
    try {
      await renameGroup(id, trimmed);
      void mutate("/groups");
    } catch (e) {
      setRenameError(e instanceof Error ? e.message : "Failed to rename group");
      void mutate("/groups"); // revert the inline edit to the server value
    }
  }

  if (error) {
    return (
      <Text font="secondary-body" color="text-02">
        {error.message}
      </Text>
    );
  }

  // No groups at all → bordered empty card with the create action (Onyx's
  // AdminListHeader empty state).
  if (!isLoading && groups.length === 0) {
    return (
      <Card rounding="lg" padding="lg">
        <div className={styles.emptyCard}>
          <Text font="main-ui-body" color="text-03">
            Create groups to organize users and manage access.
          </Text>
          <Button
            variant="action"
            size="md"
            rightIcon={SvgPlusCircle}
            onClick={() => router.push("/admin/groups/create")}
          >
            New Group
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.searchWrap}>
          <InputTypeIn
            searchIcon
            variant="internal"
            placeholder="Search groups…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button
          variant="action"
          size="md"
          rightIcon={SvgPlusCircle}
          onClick={() => router.push("/admin/groups/create")}
        >
          New Group
        </Button>
      </div>

      {renameError && (
        <div className={styles.errorRow}>
          <Text font="secondary-body" color="text-02">
            {renameError}
          </Text>
        </div>
      )}

      {isLoading ? (
        <Text font="secondary-body" color="text-03">
          Loading…
        </Text>
      ) : filtered.length === 0 ? (
        <IllustrationContent
          illustration={SvgNoResult}
          title="No groups found"
          description={`No groups matching "${query}"`}
        />
      ) : (
        <div className={styles.cards}>
          {filtered.map((g) => (
            <Card key={g.id} rounding="lg" padding="sm">
              <ContentAction
                icon={SvgUsers}
                title={g.name}
                description={groupSub(g)}
                sizePreset="main-content"
                variant="section"
                editable
                onTitleChange={(newName) => void handleRename(g.id, newName)}
                rightChildren={
                  <span className={styles.cardRight}>
                    <Text font="secondary-body" color="text-03">
                      {`${g.member_count} ${g.member_count === 1 ? "Member" : "Members"}`}
                    </Text>
                    <Button
                      icon={SvgChevronRight}
                      prominence="tertiary"
                      size="sm"
                      tooltip="View group"
                      aria-label="View group"
                      onClick={() => router.push(`/admin/groups/${g.id}`)}
                    />
                  </span>
                }
              />
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
