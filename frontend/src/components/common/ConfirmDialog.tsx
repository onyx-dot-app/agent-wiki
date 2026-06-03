"use client";

// In-app replacement for `window.confirm` on destructive actions.
//
// Usage:
//   const confirmDialog = useConfirm();
//   if (!(await confirmDialog({ title: "Delete this trigger?", confirmLabel: "Delete" }))) return;
//
// `ConfirmProvider` is mounted once in the root layout; the hook resolves
// `true` on confirm and `false` on cancel / Escape / scrim click.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Button, Text } from "@onyx-ai/opal/components";

import styles from "./ConfirmDialog.module.css";

export interface ConfirmOptions {
  title: string;
  /** Optional supporting line under the title. */
  body?: string;
  /** Label for the destructive action button. Defaults to "Confirm". */
  confirmLabel?: string;
  cancelLabel?: string;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error("useConfirm must be used within <ConfirmProvider>");
  return fn;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<boolean>((resolve) => {
      // A second request while one is open cancels the first.
      resolveRef.current?.(false);
      resolveRef.current = resolve;
      setPending(opts);
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    resolveRef.current?.(value);
    resolveRef.current = null;
    setPending(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && <ConfirmDialog opts={pending} onSettle={settle} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({
  opts,
  onSettle,
}: {
  opts: ConfirmOptions;
  onSettle: (value: boolean) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Move focus into the dialog (not onto the destructive button) so Enter
    // doesn't destroy anything by default and Escape is captured here.
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onSettle(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onSettle]);

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onSettle(false);
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className={styles.dialog}
        role="alertdialog"
        aria-modal="true"
        aria-label={opts.title}
      >
        <div className={styles.content}>
          <Text as="h2" font="main-content-emphasis">
            {opts.title}
          </Text>
          {opts.body && (
            <Text font="secondary-body" color="text-03">
              {opts.body}
            </Text>
          )}
        </div>
        <footer className={styles.footer}>
          <Button
            prominence="tertiary"
            size="md"
            onClick={() => onSettle(false)}
          >
            {opts.cancelLabel ?? "Cancel"}
          </Button>
          <Button variant="danger" size="md" onClick={() => onSettle(true)}>
            {opts.confirmLabel ?? "Confirm"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
