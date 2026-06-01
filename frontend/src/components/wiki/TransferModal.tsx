"use client";

import { useEffect, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgArrowExchange, SvgX } from "@onyx-ai/opal/icons";

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

  const { users, isLoading } = useUserSearch(query, open && pickerOpen);

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
            <h2 className={styles.title}>
              Transfer <span className={styles.titleName}>{lastSegment(path)}</span>
            </h2>
          </div>
          <button className={styles.closeBtn} onClick={onClose} aria-label="Close">
            <SvgX size={18} />
          </button>
        </header>

        <div className={styles.content}>
          <span className={styles.label}>Transfer Ownership To</span>
          <div className={styles.inputWrap}>
            <input
              className={styles.input}
              placeholder="Add a user or group"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(null);
                setPickerOpen(true);
              }}
              onFocus={() => setPickerOpen(true)}
              onBlur={() => window.setTimeout(() => setPickerOpen(false), 120)}
            />
            {pickerOpen && (
              <div className={styles.results}>
                {isLoading && users.length === 0 && (
                  <div className={styles.empty}>Searching…</div>
                )}
                {!isLoading && users.length === 0 && (
                  <div className={styles.empty}>No users found.</div>
                )}
                {users.map((u) => {
                  const isOwner = u.id === currentOwnerId;
                  return (
                    <button
                      key={u.id}
                      type="button"
                      className={styles.row}
                      disabled={isOwner}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        if (isOwner) return;
                        setSelected(u);
                        setQuery(displayName(u));
                        setPickerOpen(false);
                      }}
                    >
                      <Avatar label={initials(u)} size={28} title={displayName(u)} />
                      <span className={styles.rowText}>
                        <span className={styles.rowName}>{displayName(u)}</span>
                        <span className={styles.rowSub}>{u.email}</span>
                      </span>
                      {isOwner && <span className={styles.rowTag}>Current Owner</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <span className={styles.note}>
            The current owner is kept as an editor after transfer.
          </span>

          {error && <div className={styles.error}>{error}</div>}
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
