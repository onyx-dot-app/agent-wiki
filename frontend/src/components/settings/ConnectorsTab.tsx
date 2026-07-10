"use client";

import { useEffect, useRef, useState } from "react";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import {
  SvgArrowExchange,
  SvgCheckCircle,
  SvgClock,
  SvgLink,
  SvgMail,
  SvgSettings,
  SvgSlack,
  SvgUnplug,
  SvgVolumeOff,
  SvgX,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
import { Content, InputErrorText } from "@onyx-ai/opal/layouts";
import { cn, markdown } from "@onyx-ai/opal/utils";

import { SvgSend } from "@/components/icons/SvgSend";
import {
  ConfigRowCard,
  ConnectorModalShell,
  CountDivider,
  MiniActionButton,
} from "@/components/settings/ConnectorModal";
import { WebhookModal } from "@/components/settings/WebhookModal";
import ChipList from "@/components/inputs/ChipList";
import InputChipField, {
  type ChipItem,
} from "@/components/inputs/InputChipField";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { disconnectCraft, useCraftConnect } from "@/lib/craft";
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

const MAX_EMAILS = 5;

/** Status-coloured icons for the config row's icon slot. */
function VerifiedIcon(props: React.ComponentProps<typeof SvgCheckCircle>) {
  return (
    <SvgCheckCircle
      {...props}
      className={cn(props.className, "size-4")}
      style={{ color: "var(--status-success-05)" }}
    />
  );
}
function PendingIcon(props: React.ComponentProps<typeof SvgClock>) {
  return (
    <SvgClock
      {...props}
      className={cn(props.className, "size-4")}
      style={{ color: "var(--text-03)" }}
    />
  );
}

/** The Connectors settings tab per the mock: Slack and Emails cards with a
 * tertiary status affordance top-right, a fold line of icon actions under
 * it, and connected detail (workspace line / address tags) inset under the
 * title. The Emails card opens the address-management modal. */
export function ConnectorsTab() {
  const { status, refresh: refreshSlack, isLoading } = useSlackConnectStatus();
  const {
    status: craftStatus,
    isUnavailable: craftUnavailable,
    refresh: refreshCraft,
  } = useCraftConnect();
  const { configs, refresh: refreshConfigs } = useDestinationConfigs();
  const [emailsOpen, setEmailsOpen] = useState(false);
  const [webhooksOpen, setWebhooksOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmDialog = useConfirm();

  const emailConfigs = configs.filter((c) => c.type === "email");
  const webhookConfigs = configs.filter((c) => c.type === "webhook");

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

  async function onDisconnectCraft() {
    if (
      !(await confirmDialog({
        title: "Disconnect Onyx Craft?",
        body: "Triggers that start Craft sessions will stop until you reconnect.",
        confirmLabel: "Disconnect",
      }))
    )
      return;
    setError(null);
    try {
      await disconnectCraft();
      await refreshCraft();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to disconnect");
    }
  }

  async function onRemoveConfig(id: string) {
    setError(null);
    try {
      await deleteDestinationConfig(id);
      await refreshConfigs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    }
  }

  if (isLoading || !status) return <LoadingSpinner center />;

  return (
    <div className="flex w-full flex-col gap-2">
      <ConnectorCard
        icon={SvgSlack}
        title="Slack"
        description="Send wiki updates messages in Slack to you and your channels."
        connected={status.connected}
        connectHref={
          status.configured ? (status.connect_url ?? undefined) : undefined
        }
        unavailableNote={
          status.configured
            ? undefined
            : "An admin needs to configure the Slack app first."
        }
        detail={
          status.connected ? (
            <Text font="secondary-body" color="text-03">
              {markdown(
                `Connected in workspace **${status.team_name ?? "Slack"}**`,
              )}
            </Text>
          ) : undefined
        }
        foldActions={
          status.connected ? (
            <>
              <Button
                type="button"
                icon={SvgVolumeOff}
                size="md"
                prominence={status.muted ? "secondary" : "tertiary"}
                tooltip={
                  status.muted ? "Resume Slack delivery" : "Mute Slack delivery"
                }
                onClick={() => void onToggleMute()}
              />
              <Button
                type="button"
                icon={SvgUnplug}
                size="md"
                prominence="tertiary"
                tooltip="Disconnect Server"
                onClick={() => void onDisconnect()}
              />
            </>
          ) : undefined
        }
      />

      <ConnectorCard
        icon={SvgMail}
        title="Emails"
        description="Send wiki updates notifications to your email addresses."
        connected={emailConfigs.length > 0}
        onConnect={() => setEmailsOpen(true)}
        detail={
          emailConfigs.length > 0 ? (
            <ChipList
              items={emailConfigs.map((c) => ({ id: c.id, label: c.name }))}
              onRemove={(id) => void onRemoveConfig(id)}
              maxVisible={2}
              overflowIcon={SvgMail}
            />
          ) : undefined
        }
        foldActions={
          emailConfigs.length > 0 ? (
            <Button
              type="button"
              icon={SvgSettings}
              size="md"
              prominence="tertiary"
              tooltip="Manage"
              onClick={() => setEmailsOpen(true)}
            />
          ) : undefined
        }
      />

      <ConnectorCard
        icon={SvgLink}
        title="Webhooks"
        description="POST wiki updates to Zapier, n8n, Make, or any HTTP endpoint."
        connected={webhookConfigs.length > 0}
        onConnect={() => setWebhooksOpen(true)}
        detail={
          webhookConfigs.length > 0 ? (
            <ChipList
              items={webhookConfigs.map((c) => ({ id: c.id, label: c.name }))}
              onRemove={(id) => void onRemoveConfig(id)}
              maxVisible={6}
              overflowIcon={SvgLink}
            />
          ) : undefined
        }
        foldActions={
          webhookConfigs.length > 0 ? (
            <Button
              type="button"
              icon={SvgSettings}
              size="md"
              prominence="tertiary"
              tooltip="Manage"
              onClick={() => setWebhooksOpen(true)}
            />
          ) : undefined
        }
      />

      <ConnectorCard
        icon={SvgOnyxLogo}
        title="Onyx Craft"
        description="Start a Craft session in Onyx when a trigger fires."
        connected={Boolean(craftStatus?.connected)}
        connectHref={craftStatus?.connect_url ?? undefined}
        unavailableNote={
          craftUnavailable
            ? "An admin needs to configure the Onyx connection first."
            : undefined
        }
        detail={
          craftStatus?.connected ? (
            <Text font="secondary-body" color="text-03">
              {markdown(
                `Connected as **${craftStatus.onyx_user_email ?? "you"}**`,
              )}
            </Text>
          ) : undefined
        }
        foldActions={
          craftStatus?.connected ? (
            <Button
              type="button"
              icon={SvgUnplug}
              size="md"
              prominence="tertiary"
              tooltip="Disconnect Onyx Craft"
              onClick={() => void onDisconnectCraft()}
            />
          ) : undefined
        }
      />

      {error && <InputErrorText type="error">{error}</InputErrorText>}

      {emailsOpen && (
        <EmailsModal
          configs={emailConfigs}
          refresh={refreshConfigs}
          onClose={() => setEmailsOpen(false)}
        />
      )}

      {webhooksOpen && (
        <WebhookModal
          configs={webhookConfigs}
          refresh={refreshConfigs}
          onClose={() => setWebhooksOpen(false)}
        />
      )}
    </div>
  );
}

/** Mock card anatomy: rounded-12 bordered shell with 4px padding, an 8px
 * title section (20px logo + title/description), connected detail inset
 * 24px under the title, and a right column of status + fold-line actions.
 * Connected cards sit on tint-00; disconnected on neutral-01. */
function ConnectorCard({
  icon: Icon,
  title,
  description,
  connected,
  connectHref,
  onConnect,
  unavailableNote,
  detail,
  foldActions,
}: {
  icon: IconFunctionComponent;
  title: string;
  description: string;
  connected: boolean;
  connectHref?: string;
  onConnect?: () => void;
  unavailableNote?: string;
  detail?: React.ReactNode;
  foldActions?: React.ReactNode;
}) {
  return (
    <div className="w-full [&_.opal-content-md-icon-container_svg]:!size-[18px]">
      <Card
        padding="xs"
        rounding="md"
        border="solid"
        background={connected ? "light" : "heavy"}
      >
        <div className="flex w-full items-start">
          <div className="flex min-w-0 flex-1 flex-col p-2">
            <Content
              sizePreset="main-ui"
              variant="section"
              icon={Icon}
              title={title}
              description={description}
            />
            {detail && (
              <div className="w-full pt-1 pl-6 [&>span]:block">{detail}</div>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end">
            {connected ? (
              <span className="flex items-center gap-1 p-2">
                <Text font="main-ui-action" color="text-03" nowrap>
                  Connected
                </Text>
                <SvgCheckCircle className="size-4 text-(--status-success-05)" />
              </span>
            ) : connectHref ? (
              <Button
                size="sm"
                prominence="tertiary"
                rightIcon={SvgArrowExchange}
                href={connectHref}
              >
                Connect
              </Button>
            ) : onConnect ? (
              <Button
                type="button"
                size="sm"
                prominence="tertiary"
                rightIcon={SvgArrowExchange}
                onClick={onConnect}
              >
                Connect
              </Button>
            ) : (
              <span className="p-2">
                <Text font="secondary-body" color="text-03" nowrap>
                  {unavailableNote ?? "Unavailable"}
                </Text>
              </span>
            )}
            {foldActions && (
              <div className="flex items-center justify-end gap-1 p-1">
                {foldActions}
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}

/** The mock's Emails modal: 480px three-zone alert (white header with a
 * stacked mail icon, content on a raised white panel, white Done footer),
 * a chip input capped at MAX_EMAILS addresses with a Send button that
 * appears once drafts exist, and per-address rows with status icon,
 * verify/cooldown action, and remove. */
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
  const [drafts, setDrafts] = useState<ChipItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentBanner, setSentBanner] = useState(false);
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

  const atCap = configs.length + drafts.length >= MAX_EMAILS;

  function addDraft(value: string) {
    const address = value.trim();
    if (!address) return;
    if (!address.includes("@")) {
      setError("enter a valid email address");
      return;
    }
    if (atCap) {
      setError(`up to ${MAX_EMAILS} addresses`);
      return;
    }
    if (
      drafts.some((d) => d.label === address) ||
      configs.some((c) => c.name === address)
    ) {
      setDraft("");
      return;
    }
    setDrafts((cur) => [...cur, { id: address, label: address }]);
    setDraft("");
    setError(null);
  }

  async function onSend() {
    if (busy || drafts.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      for (const d of drafts) {
        const { id, verificationError } = await ensureEmailDestination(
          configs,
          d.label,
        );
        if (!mountedRef.current) return;
        if (verificationError) setError(verificationError);
        else startCooldown(id, 60);
      }
      await refresh();
      if (!mountedRef.current) return;
      setDrafts([]);
      setSentBanner(true);
    } catch (e) {
      if (mountedRef.current)
        setError(e instanceof Error ? e.message : "failed to add address");
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }

  async function onResend(c: DestinationConfig) {
    setError(null);
    const result = await resendVerification(c.id);
    if (!mountedRef.current) return;
    if (result.ok) {
      setSentBanner(true);
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

  const banner = sentBanner && (
    <div
      className="fixed top-6 left-1/2 flex w-[min(480px,92vw)] -translate-x-1/2 items-start gap-2 rounded-(--radius-12) border border-(--status-info-03) bg-(--background-tint-00) p-3 shadow-(--shadow-modal)"
      onClick={(e) => e.stopPropagation()}
    >
      <SvgSend className="mt-[2px] size-4 shrink-0 text-(--status-info-05)" />
      <div className="flex min-w-0 flex-1 flex-col">
        <Text font="main-ui-action" color="text-04">
          Check your email inbox.
        </Text>
        <Text font="secondary-body" color="text-03">
          We&apos;ve sent a verification link to your email address.
        </Text>
      </div>
      <Button
        type="button"
        icon={SvgX}
        size="sm"
        prominence="tertiary"
        tooltip="Dismiss"
        onClick={() => setSentBanner(false)}
      />
    </div>
  );

  return (
    <ConnectorModalShell
      icon={SvgMail}
      title="Emails"
      description="Send wiki updates notifications to your email addresses."
      onClose={onClose}
      banner={banner}
    >
      <div className="flex w-full items-center gap-1">
        <div className="min-w-0 flex-1">
          <InputChipField
            chips={drafts}
            onRemoveChip={(id: string) =>
              setDrafts((cur) => cur.filter((d) => d.id !== id))
            }
            onAdd={addDraft}
            value={draft}
            onChange={setDraft}
            placeholder="Add an email…"
            disabled={busy}
          />
        </div>
        {drafts.length > 0 && (
          <Button
            type="button"
            variant="action"
            icon={SvgSend}
            disabled={busy}
            onClick={() => void onSend()}
          >
            Send
          </Button>
        )}
      </div>

      {error && <InputErrorText type="error">{error}</InputErrorText>}

      <div className="flex w-full flex-col gap-1">
        {configs.map((c) => {
          const deadline = cooldowns.get(c.id) ?? 0;
          const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
          return (
            <ConfigRowCard
              key={c.id}
              icon={c.verified_at ? VerifiedIcon : PendingIcon}
              title={c.name}
              description={c.verified_at ? "Verified" : "Not verified"}
            >
              {!c.verified_at && (
                <MiniActionButton
                  icon={SvgSend}
                  disabled={remaining > 0}
                  onClick={() => void onResend(c)}
                >
                  {remaining > 0 ? `${remaining}s` : "Verify"}
                </MiniActionButton>
              )}
              <Button
                type="button"
                icon={SvgX}
                size="sm"
                prominence="tertiary"
                tooltip="Remove address"
                onClick={() => void onDelete(c)}
              />
            </ConfigRowCard>
          );
        })}
        <CountDivider count={configs.length} noun="Email" />
      </div>
    </ConnectorModalShell>
  );
}
