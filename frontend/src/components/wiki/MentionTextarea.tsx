"use client";

import { useEffect, useRef, useState } from "react";

import { activeMentionQuery } from "@/lib/commentMentions";
import { displayName, initials, useUserSearch } from "@/lib/users";

import styles from "./MentionTextarea.module.css";

/** A comment textarea with a Google-Docs-style `@` people typeahead. The
 * parent owns the (display-text) value. On each pick we report
 * `("@Name", userId)` so the parent can tokenize the body on submit.
 * Enter submits (chat semantics), Shift+Enter inserts a newline, and while
 * the menu is open Enter picks the highlighted mention instead. */
export function MentionTextarea({
  value,
  onChange,
  onPickMention,
  onSubmit,
  placeholder,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onPickMention: (display: string, userId: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  // `query === null` means no active mention; "" means just typed "@".
  const [query, setQuery] = useState<string | null>(null);
  const [start, setStart] = useState(0);
  const [sel, setSel] = useState(0);

  const { users } = useUserSearch(query ?? "", query !== null);
  const open = query !== null && users.length > 0;

  // A textarea does not grow with its content, so without this it stays at
  // whatever height CSS gives it and long comments scroll inside one cramped
  // line. Measuring needs the height released first: `scrollHeight` on an
  // element already stretched by an inline height reports that height, so it
  // could never shrink again after a delete. The floor and ceiling stay in CSS
  // (`.comment-input textarea`) — `min-height` holds the field open at three
  // lines while it's short, and `max-height` takes over once it's long enough
  // to scroll, so neither bound has to be duplicated here as a magic number.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  // Recompute the active `@query` from the current caret position.
  const sync = (v: string, caret: number) => {
    const m = activeMentionQuery(v, caret);
    if (m) {
      setQuery(m.query);
      setStart(m.start);
      setSel(0);
    } else {
      setQuery(null);
    }
  };

  const pick = (userId: string, display: string) => {
    const el = ref.current;
    const caret = el ? el.selectionStart : value.length;
    const inserted = `@${display} `;
    const next = value.slice(0, start) + inserted + value.slice(caret);
    onChange(next);
    onPickMention(`@${display}`, userId);
    setQuery(null);
    // Restore the caret just after the inserted mention once React re-renders.
    const pos = start + inserted.length;
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(pos, pos);
      }
    });
  };

  return (
    <div className={styles.wrap}>
      {/* raw-ok: Opal has no multiline text entry (InputTypeIn is single-line) and the mention typeahead needs direct caret access */}
      <textarea
        ref={ref}
        className={styles.textarea}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        rows={1}
        onChange={(e) => {
          onChange(e.target.value);
          sync(e.target.value, e.target.selectionStart);
        }}
        onClick={(e) => sync(value, e.currentTarget.selectionStart)}
        onKeyUp={(e) => {
          // Caret moved by arrows/home/end (when the menu isn't catching them).
          if (!open) sync(value, e.currentTarget.selectionStart);
        }}
        onKeyDown={(e) => {
          if (!open) {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
            return;
          }
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setSel((s) => (s + 1) % users.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSel((s) => (s - 1 + users.length) % users.length);
          } else if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            const u = users[sel];
            if (u) pick(u.id, displayName(u));
          } else if (e.key === "Escape") {
            e.preventDefault();
            setQuery(null);
          }
        }}
      />
      {open && (
        <ul className={styles.menu} role="listbox">
          {users.map((u, i) => (
            <li
              key={u.id}
              role="option"
              aria-selected={i === sel}
              className={`${styles.item} ${i === sel ? styles.itemActive : ""}`}
              // onMouseDown (not onClick) so the textarea doesn't blur first.
              onMouseDown={(e) => {
                e.preventDefault();
                pick(u.id, displayName(u));
              }}
              onMouseEnter={() => setSel(i)}
            >
              <span className={styles.avatar}>{initials(u)}</span>
              <span className={styles.name}>{displayName(u)}</span>
              {u.name && <span className={styles.email}>{u.email}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
