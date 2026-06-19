"use client";

import { useEffect, useRef } from "react";
import { mutate as globalMutate } from "swr";

import { toast } from "@/hooks/useToast";
import { useAgentSessions } from "@/lib/launchers";

export function craftFailureMessage(reason: string | null): string {
  switch (reason) {
    case "auth_expired":
      return "Your Onyx connection expired — reconnect under Agents → Onyx Craft.";
    case "org_at_capacity":
      return "Onyx is at capacity — try again shortly.";
    case "rate_limited":
      return "Too many Craft launches — wait a moment.";
    case "onyx_unreachable":
      return "Couldn't reach Onyx — try again.";
    default:
      return "Couldn't start the Craft build.";
  }
}

/**
 * Render-null. Watches the user's Craft sessions (the agent-sessions poll)
 * and fires a transient toast the moment one flips `provisioning → ready`
 * or `→ failed`. The durable, clickable "Open Craft" lives in the
 * notification bell + the page's active-agents bar; this is just the nudge.
 */
export function CraftNotifier() {
  const { sessions } = useAgentSessions();
  const lastStatus = useRef<Map<string, string>>(new Map());
  const primed = useRef(false);

  useEffect(() => {
    const craft = sessions.filter((s) => s.tool_id === "onyx-craft");

    // First poll after mount: record current statuses without toasting, so a
    // session that was already ready before we mounted doesn't re-announce.
    if (!primed.current) {
      craft.forEach((s) => lastStatus.current.set(s.id, s.status));
      primed.current = true;
      return;
    }

    let resolved = false;
    for (const s of craft) {
      const prev = lastStatus.current.get(s.id);
      lastStatus.current.set(s.id, s.status);
      // Fire on any un-announced arrival at a terminal state. We don't require
      // prev==="provisioning": if the tab was backgrounded during provisioning
      // (SWR pauses polling while hidden) the session first surfaces as ready,
      // and the user still needs the toast. The `prev === s.status` skip + the
      // priming pass keep this from re-announcing a state we've already shown.
      if (prev === s.status) continue;
      if (s.status === "ready") {
        const url = s.external_url;
        toast.success("Craft is ready", {
          description: "Your build is ready to open.",
          duration: 15000,
          action: url
            ? {
                label: "Open Craft",
                onClick: () =>
                  window.open(url, "_blank", "noopener,noreferrer"),
              }
            : undefined,
        });
        resolved = true;
      } else if (s.status === "failed") {
        toast.error("Craft launch failed", {
          description: craftFailureMessage(s.failure_reason),
        });
        resolved = true;
      }
    }
    // A terminal transition means the worker wrote a notification — refresh
    // the bell so its badge updates without waiting for a focus revalidate.
    if (resolved) void globalMutate("/notifications");
  }, [sessions]);

  return null;
}
