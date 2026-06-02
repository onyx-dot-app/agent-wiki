"use client";

import { useEffect, useState } from "react";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import { SvgArrowExchange, SvgUser, SvgX } from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";

import { Avatar } from "@/components/common/Avatar";
import { transferOwnership } from "@/lib/permissions";
import { displayName, initials, useUserSearch, type UserLite } from "@/lib/users";

import styles from "./TransferModal.module.css";

interface TransferModalProps {
  path: string;
  currentOwnerId: string | null;
  open: boolean;
  onClose: () => void;
  onTransferred: () => void;
}

function lastSegment(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "Wiki";
  const seg = clean.split("/").pop() ?? clean;
  return seg.endsWith(".md") ? seg.slice(0, -3) : seg;
}

export function TransferModal({
  path,
  currentOwnerId,
  open,
  onClose,
  onTransferred,
}: TransferModalProps) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<UserLite | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { users } = useUserSearch(query, open && pickerOpen);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const transfer = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await transferOwnership(path, selected.id);
      onTransferred();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transfer failed");
    } finally {
      setBusy(false);
    }
  };

  const showResults = pickerOpen && users.length > 0;

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={`Transfer ${lastSegment(path)}`}
      >
        <header className={styles.header}>
          <span className={styles.headerIcon}>
            <SvgArrowExchange size={20} />
          </span>
          <div className={styles.headerText}>
            <Text as="h2" font="main-content-emphasis">
              {markdown(`Transfer *${lastSegment(path)}*`)}
            </Text>
          </div>
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgX}
            tooltip="Close"
            onClick={onClose}
          />
        </header>

        <div className={styles.content}>
          <Text font="secondary-action" color="text-02">
            Transfer Ownership To
          </Text>
          <Popover
            open={showResults}
            onOpenChange={(o) => {
              if (!o) setPickerOpen(false);
            }}
          >
            <Popover.Anchor asChild>
              <div className={styles.anchorWrap}>
                <InputTypeIn
                  searchIcon
                  placeholder="Add a user"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setSelected(null);
                    setPickerOpen(true);
                  }}
                  onFocus={() => setPickerOpen(true)}
                />
              </div>
            </Popover.Anchor>
            <Popover.Content
              width="trigger"
              align="start"
              sideOffset={4}
              container={typeof document !== "undefined" ? document.body : undefined}
              onOpenAutoFocus={(e) => e.preventDefault()}
              onCloseAutoFocus={(e) => e.preventDefault()}
            >
              <PopoverMenu>
                {users.map((u) => {
                  const isOwner = u.id === currentOwnerId;
                  return (
                    <LineItemButton
                      key={u.id}
                      icon={SvgUser}
                      title={displayName(u)}
                      description={u.email}
                      sizePreset="main-ui"
                      variant="section"
                      rightChildren={
                        isOwner ? (
                          <Text font="secondary-body" color="text-03">
                            Current Owner
                          </Text>
                        ) : undefined
                      }
                      onClick={() => {
                        if (isOwner) return;
                        setSelected(u);
                        setQuery("");
                        setPickerOpen(false);
                      }}
                    />
                  );
                })}
              </PopoverMenu>
            </Popover.Content>
          </Popover>

          {selected && (
            <div className={styles.selectedRow}>
              <Avatar
                label={initials(selected)}
                size={28}
                title={displayName(selected)}
              />
              <div className={styles.rowText}>
                <Text font="main-ui-body" nowrap>
                  {displayName(selected)}
                </Text>
                {selected.email && (
                  <Text font="secondary-body" color="text-03" nowrap>
                    {selected.email}
                  </Text>
                )}
              </div>
              <Button
                prominence="tertiary"
                size="sm"
                icon={SvgX}
                tooltip="Remove"
                onClick={() => setSelected(null)}
              />
            </div>
          )}

          <Text font="secondary-body" color="text-03">
            The current owner is kept as an editor after transfer.
          </Text>

          {error && (
            <Text font="secondary-body" color="text-02">
              {error}
            </Text>
          )}
        </div>

        <footer className={styles.footer}>
          <Button prominence="tertiary" size="md" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="action"
            size="md"
            disabled={!selected || busy}
            onClick={() => void transfer()}
          >
            {busy ? "Transferring…" : "Transfer"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
