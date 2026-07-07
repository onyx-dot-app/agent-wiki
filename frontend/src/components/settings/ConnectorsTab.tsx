"use client";

import { useEffect, useRef, useState } from "react";

import {
  Button,
  LinkButton,
  Text,
  Card,
  InputTypeIn,
} from "@onyx-ai/opal/components";
import {
  SvgVolumeOff,
  SvgCheckCircle,
  SvgMail,
  SvgSlack,
  SvgTrash,
  SvgX,
} from "@onyx-ai/opal/icons";
import { InputErrorText, Content, ContentAction } from "@onyx-ai/opal/layouts";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { ensureEmailDestination } from "@/lib/emailConnect";
import {
  disconnectSlack,
  setSlackMuted,
  useSlackConnectStatus,
} from "@/lib/slackConnect";
import {
  deleteDestinationConfig,
  resendVerification,
  useDestinationConfigs,
  type DestinationConfig,
} from "@/lib/triggers";

/** The Connectors settings tab: the Slack and Emails cards from the mock,
 * with the Emails card opening the address-management modal. */
export function ConnectorsTab() {
  const { status, refresh: refreshSlack, isLoading } = useSlackConnectStatus();
  const { configs, refresh: refreshConfigs } = useDestinationConfigs();
  const [emailsOpen, setEmailsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmDialog = useConfirm();

  const emailConfigs = configs.filter((c) => c.type === "email");

  async function onDisconnect() {
    if (
      !(await confirmDialog({
        title: "Disconnect Slack?",
        body: "Triggers that send to Slack will stop delivering until you reconnect.",
        confirmLabel: "Disconnect",
      }))
    )
      return;
    setError(null);
    try {
      await disconnectSlack();
      await refreshSlack();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to disconnect");
    }
  }

  async function onToggleMute() {
    if (!status) return;
    setError(null);
    try {
      await setSlackMuted(!status.muted, status.team_id);
      await refreshSlack();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update");
    }
  }

  if (isLoading || !status) return <LoadingSpinner center />;

  return (
    <div className="flex w-full flex-col gap-4">
      <ConnectorCard
        icon={SvgSlack}
        title="Slack"
        description="Send wiki updates as Slack messages to you and your channels."
        connected={status.connected}
        connectHref={
          status.configured ? (status.connect_url ?? undefined) : undefined
        }
        unavailableNote={
          status.configured
            ? undefined
            : "An admin needs to configure the Slack app first."
        }
      >
        {status.connected && (
          <div className="flex w-full items-center justify-between">
            <Text font="secondary-body" color="text-03">
              {`Connected in workspace ${status.team_name ?? "Slack"}${
                status.muted ? " (muted)" : ""
              }`}
            </Text>
            <span className="flex items-center gap-1">
              <Button
                type="button"
                icon={SvgVolumeOff}
                size="sm"
                prominence={status.muted ? "secondary" : "tertiary"}
                tooltip={
                  status.muted ? "Resume Slack delivery" : "Mute Slack delivery"
                }
                onClick={() => void onToggleMute()}
              />
              <Button
                type="button"
                size="sm"
                variant="danger"
                prominence="secondary"
                onClick={() => void onDisconnect()}
              >
                Disconnect
              </Button>
            </span>
          </div>
        )}
      </ConnectorCard>

      <ConnectorCard
        icon={SvgMail}
        title="Emails"
        description="Send wiki updates as notifications to your email addresses."
        connected={emailConfigs.length > 0}
        onManage={() => setEmailsOpen(true)}
        manageLabel={emailConfigs.length > 0 ? "Manage" : "Add addresses"}
      >
        {emailConfigs.length > 0 && (
          <div className="flex w-full flex-wrap items-center gap-1">
            {emailConfigs.map((c) => (
              <span
                key={c.id}
                className="flex items-center rounded-(--radius-04) bg-(--background-tint-02) px-1 py-[2px]"
              >
                <Text font="secondary-body" color="text-03" nowrap>
                  {c.verified_at ? c.name : `${c.name} (unverified)`}
                </Text>
              </span>
            ))}
          </div>
        )}
      </ConnectorCard>

      {error && <InputErrorText type="error">{error}</InputErrorText>}

      {emailsOpen && (
        <EmailsModal
          configs={emailConfigs}
          refresh={refreshConfigs}
          onClose={() => setEmailsOpen(false)}
        />
      )}
    </div>
  );
}

function ConnectorCard({
  icon,
  title,
  description,
  connected,
  connectHref,
  unavailableNote,
  onManage,
  manageLabel,
  children,
}: {
  icon: IconFunctionComponent;
  title: string;
  description: string;
  connected: boolean;
  connectHref?: string;
  unavailableNote?: string;
  onManage?: () => void;
  manageLabel?: string;
  children?: React.ReactNode;
}) {
  return (
    <Card padding="md">
      <div className="flex w-full flex-col gap-2">
        <ContentAction
          sizePreset="main-ui"
          variant="section"
          icon={icon}
          title={title}
          description={description}
          rightChildren={
            <span className="flex shrink-0 items-center gap-1">
              {connected ? (
                <>
                  <Text font="main-ui-action" color="text-04" nowrap>
                    Connected
                  </Text>
                  <SvgCheckCircle className="size-4 text-(--status-success-05)" />
                  {onManage && (
                    <Button
                      type="button"
                      size="sm"
                      prominence="secondary"
                      onClick={onManage}
                    >
                      {manageLabel ?? "Manage"}
                    </Button>
                  )}
                </>
              ) : connectHref ? (
                <LinkButton href={connectHref} target="_self">
                  Connect
                </LinkButton>
              ) : onManage ? (
                <Button
                  type="button"
                  size="sm"
                  prominence="secondary"
                  onClick={onManage}
                >
                  {manageLabel ?? "Connect"}
                </Button>
              ) : (
                <Text font="secondary-body" color="text-03" nowrap>
                  {unavailableNote ?? "Unavailable"}
                </Text>
              )}
            </span>
          }
        />
        {children}
      </div>
    </Card>
  );
}

function EmailsModal({
  configs,
  refresh,
  onClose,
}: {
  configs: DestinationConfig[];
  refresh: () => Promise<unknown>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  // Per-config resend cooldown deadlines (epoch ms), driven by the server's
  // retry_after_seconds.
  const [cooldowns, setCooldowns] = useState<Map<string, number>>(new Map());
  const [now, setNow] = useState(() => Date.now());
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Async handlers must not set state once the modal has unmounted.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const hasCooldowns = [...cooldowns.values()].some((t) => t > now);
  useEffect(() => {
    if (!hasCooldowns) return;
    tickRef.current = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, [hasCooldowns]);

  function startCooldown(configId: string, seconds: number) {
    setCooldowns((cur) =>
      new Map(cur).set(configId, Date.now() + seconds * 1000),
    );
    setNow(Date.now());
  }

  async function onAdd() {
    const address = draft.trim();
    if (!address || busy) return;
    if (!address.includes("@")) {
      setError("enter a valid email address");
      return;
    }
    setBusy(true);
    setError(null);
    setSentTo(null);
    try {
      const { id, verificationError } = await ensureEmailDestination(
        configs,
        address,
      );
      await refresh();
      if (!mountedRef.current) return;
      setDraft("");
      if (verificationError) setError(verificationError);
      else {
        setSentTo(address);
        startCooldown(id, 60);
      }
    } catch (e) {
      if (mountedRef.current)
        setError(e instanceof Error ? e.message : "failed to add address");
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }

  async function onResend(c: DestinationConfig) {
    setError(null);
    setSentTo(null);
    const result = await resendVerification(c.id);
    if (!mountedRef.current) return;
    if (result.ok) {
      setSentTo(c.name);
      startCooldown(c.id, 60);
    } else if (result.retryAfterSeconds) {
      startCooldown(c.id, result.retryAfterSeconds);
    } else if (result.error) {
      setError(result.error);
    }
  }

  async function onDelete(c: DestinationConfig) {
    setError(null);
    try {
      await deleteDestinationConfig(c.id);
      await refresh();
    } catch (e) {
      if (mountedRef.current)
        setError(e instanceof Error ? e.message : "failed to remove");
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-(--mask-03)"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-[min(560px,92vw)] flex-col gap-3 overflow-y-auto rounded-(--radius-12) bg-(--background-tint-00) p-6 shadow-(--shadow-modal)"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex w-full items-center justify-between">
          <Text font="main-content-emphasis" color="text-04">
            Emails
          </Text>
          <Button
            type="button"
            icon={SvgX}
            size="sm"
            prominence="tertiary"
            tooltip="Close"
            onClick={onClose}
          />
        </div>
        <Text font="secondary-body" color="text-03">
          Addresses must be verified before triggers can email them. We send a
          link, and clicking it verifies the address.
        </Text>

        <div className="flex w-full items-center gap-2">
          <div className="min-w-0 flex-1">
            <InputTypeIn
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void onAdd();
                }
              }}
              placeholder="name@company.com"
            />
          </div>
          <Button
            type="button"
            variant="action"
            disabled={busy || !draft.trim()}
            onClick={() => void onAdd()}
          >
            Send
          </Button>
        </div>

        {sentTo && (
          <Text font="secondary-body" color="status-success-05">
            {`Verification sent to ${sentTo}. Check that inbox.`}
          </Text>
        )}
        {error && <InputErrorText type="error">{error}</InputErrorText>}

        <div className="flex w-full flex-col">
          {configs.length === 0 && (
            <Text font="secondary-body" color="text-03">
              No addresses yet.
            </Text>
          )}
          {configs.map((c) => {
            const deadline = cooldowns.get(c.id) ?? 0;
            const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
            return (
              <div
                key={c.id}
                className="flex w-full items-center justify-between gap-2 border-b border-(--border-01) py-2 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <Content
                    sizePreset="main-ui"
                    variant="section"
                    title={c.name}
                    tag={
                      c.verified_at
                        ? { title: "Verified", color: "green" }
                        : { title: "Not verified", color: "amber" }
                    }
                  />
                </div>
                <span className="flex shrink-0 items-center gap-1">
                  {!c.verified_at &&
                    (remaining > 0 ? (
                      <Text font="secondary-body" color="text-03" nowrap>
                        {`Resend in ${remaining}s`}
                      </Text>
                    ) : (
                      <LinkButton onClick={() => void onResend(c)}>
                        Verify
                      </LinkButton>
                    ))}
                  <Button
                    type="button"
                    icon={SvgTrash}
                    size="sm"
                    prominence="tertiary"
                    tooltip="Remove address"
                    onClick={() => void onDelete(c)}
                  />
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
