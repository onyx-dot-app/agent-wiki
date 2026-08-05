"use client";

// Chat mode of the floating wiki toolbar: idle composer, thinking states,
// streaming/finished response, reply. Frames 2288:88246/88606, 2293:93744,
// 2293:92812.
import { useMemo, useState } from "react";

import {
  Button,
  CompactMarkdown,
  IconContainer,
  ShadowDiv,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgSquare } from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";

import {
  Composer,
  ModelBar,
  QueryChips,
  ResponseActions,
  SourceChips,
  ThinkingShimmer,
  type ToolbarContext,
} from "@/components/wiki/toolbar/chatParts";
import type { WikiChat } from "@/components/wiki/toolbar/useWikiChat";
import { queryChipsFromItems, sourcesFromItems } from "@/lib/chatState";

interface ChatErrorProps {
  message: string;
  onRetry?: () => void;
}

function ChatError({ message, onRetry }: ChatErrorProps) {
  return (
    <Section
      flexDirection="row"
      justifyContent="between"
      gap={0.25}
      padding={0}
      height="fit"
    >
      <Text font="secondary-body" color="status-error-05">
        {message}
      </Text>
      {onRetry && (
        <Button
          variant="danger"
          prominence="secondary"
          size="sm"
          onClick={onRetry}
        >
          Retry
        </Button>
      )}
    </Section>
  );
}

interface ToolbarChatProps {
  chat: WikiChat;
  contexts: ToolbarContext[];
  onRemoveContext?: (path: string) => void;
  onAttachContext?: (path: string) => void;
}

export function ToolbarChat({
  chat,
  contexts,
  onRemoveContext,
  onAttachContext,
}: ToolbarChatProps) {
  const [draft, setDraft] = useState("");

  const answer = useMemo(() => {
    for (let i = chat.items.length - 1; i >= 0; i--) {
      const it = chat.items[i];
      if (it.kind === "assistant") return it;
      if (it.kind === "user") break;
    }
    return null;
  }, [chat.items]);

  // Query chips come from the current turn's tool calls only.
  const turnItems = useMemo(() => {
    const lastUser = chat.items.map((i) => i.kind).lastIndexOf("user");
    return chat.items.slice(lastUser + 1);
  }, [chat.items]);
  const turnQueries = useMemo(
    () => queryChipsFromItems(turnItems),
    [turnItems],
  );
  const turnSources = useMemo(() => sourcesFromItems(turnItems), [turnItems]);

  function submit() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    chat.send(text);
  }

  const hasTurn = chat.items.length > 0;
  const answerId = answer?.id;

  if (!hasTurn) {
    return (
      <Section gap={0.25} padding={0} height="fit" alignItems="stretch">
        {chat.error && <ChatError message={chat.error} onRetry={chat.retry} />}
        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={submit}
          placeholder="Ask wiki or write with AI…"
          contexts={contexts}
          onRemoveContext={onRemoveContext}
          onAttachContext={onAttachContext}
          autoFocus
        />
      </Section>
    );
  }

  return (
    <Section gap={0.25} padding={0} height="fit" alignItems="stretch">
      {/* Pre-text turn: one white composer-shaped card holding the active
          step, the assistant identity, and the stop CTA (mock 2293:93745
          Footer: flex-1 Identity, dark CTA right). */}
      {chat.thinking ? (
        <Section
          gap={0}
          padding={0}
          height="fit"
          alignItems="stretch"
          className="overflow-hidden rounded-12 bg-background-tint-00"
        >
          <Section gap={0.25} padding={0.25} height="fit" alignItems="stretch">
            {/* Step line reserves the mock's 28px (2293:92303 min-h). */}
            <Section
              gap={0}
              padding={0.25}
              height="fit"
              alignItems="start"
              justifyContent="center"
              className="min-h-7"
            >
              <ThinkingShimmer />
            </Section>
            {turnQueries.length > 0 && (
              <Section gap={0} padding={0.25} height="fit" alignItems="stretch">
                <QueryChips queries={turnQueries} />
              </Section>
            )}
          </Section>
          <Section
            flexDirection="row"
            justifyContent="between"
            gap={0.25}
            padding={0.25}
            height="fit"
            alignItems="end"
          >
            <IconContainer size="main-ui" avatar="icon" icon={SvgOnyxLogo} />
            <Button
              icon={SvgSquare}
              prominence="primary"
              size="lg"
              tooltip="Stop"
              onClick={chat.stop}
            />
          </Section>
        </Section>
      ) : (
        /* Streamed/finished text on the panel surface, clamped to the
           mock's 120px window with the fade above (2293:92829). */
        <Section gap={0.25} padding={0} height="fit" alignItems="stretch">
          {/* raw-ok: the mock's response Text layer has a 12px horizontal
              inset. Section numeric padding is uniform and would re-add
              vertical height to the 120px response window. */}
          <div className="w-full px-3">
            <ShadowDiv className="max-h-[120px] overflow-y-auto">
              <CompactMarkdown>{answer?.content ?? ""}</CompactMarkdown>
            </ShadowDiv>
          </div>
          {chat.error && (
            <ChatError
              message={chat.error}
              onRetry={!answer?.content ? chat.retry : undefined}
            />
          )}
        </Section>
      )}
      {chat.thinking && chat.error && (
        <Section gap={0.25} padding={0.25} height="fit" alignItems="stretch">
          <ChatError message={chat.error} onRetry={chat.retry} />
        </Section>
      )}
      {/* Actions row between response and reply (mock 2302:95738):
          copy/rate/retry left, model bar right. */}
      {!chat.sending && answer?.content && (
        <Section
          flexDirection="row"
          justifyContent="between"
          gap={0.25}
          padding={0.25}
          height="fit"
        >
          <ResponseActions
            text={answer.content}
            feedback={answer.feedback}
            // Rating addresses a persisted turn, so it waits for the id.
            onFeedback={
              answerId
                ? (feedback) =>
                    chat.rate(
                      answerId,
                      answer.feedback === feedback ? null : feedback,
                    )
                : undefined
            }
            onRetry={chat.retry}
          />
          <Section
            flexDirection="row"
            gap={0.25}
            padding={0}
            width="fit"
            height="fit"
            alignItems="center"
          >
            <SourceChips paths={turnSources} />
            <ModelBar />
          </Section>
        </Section>
      )}
      {/* Below the body: assistant identity while text streams (the thinking
          card carries its own), reply composer when the turn is done. */}
      {chat.sending ? (
        !chat.thinking && (
          <Section
            flexDirection="row"
            justifyContent="between"
            gap={0.25}
            padding={0.25}
            height="fit"
          >
            <IconContainer size="main-ui" avatar="icon" icon={SvgOnyxLogo} />
            <Button
              icon={SvgSquare}
              prominence="primary"
              size="lg"
              tooltip="Stop"
              onClick={chat.stop}
            />
          </Section>
        )
      ) : (
        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={submit}
          placeholder="Reply…"
          contexts={contexts}
          onRemoveContext={onRemoveContext}
          onAttachContext={onAttachContext}
        />
      )}
    </Section>
  );
}
