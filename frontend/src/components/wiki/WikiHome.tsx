"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  EdgeScrollbar,
  useElementScrollTarget,
} from "@/components/wiki/EdgeScrollbar";
import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";
import useSWR from "swr";
import {
  Button,
  Divider,
  InputTypeIn,
  SelectCard,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgArrowUp,
  SvgMoreHorizontal,
  SvgOnyxOctagon,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { relativeTime } from "@/lib/time";
import { AI_DRAFT_KEY } from "@/lib/wiki/constants";
import { generateDraft } from "@/lib/wiki/svc";
import type { RecentPage } from "@/lib/wiki/types";
import { wikiHref, wikiPath } from "@/lib/wikiHref";
import { StartNewPage } from "@/components/wiki/StartNewPage";
import WikiItemMenu from "@/components/wiki/WikiItemActions";
import { WikiToolbarDock } from "@/components/wiki/toolbar/WikiToolbar";
import styles from "@/components/wiki/WikiHome.module.css";

export function WikiHome() {
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollTarget = useElementScrollTarget(scrollRef);
  const [aiPrompt, setAiPrompt] = useState("");
  const [generating, setGenerating] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const { data: recentData } = useSWR<{ pages: RecentPage[] }>(
    "/wiki/recent?limit=12",
  );
  const recent = recentData?.pages ?? [];

  async function onAiSubmit(e: FormEvent) {
    e.preventDefault();
    const prompt = aiPrompt.trim();
    if (!prompt || generating) return;
    setGenerating(true);
    setAiError(null);
    try {
      // Generate a full draft up front, then drop the user into the New
      // Document composer with it pre-filled to review and create. The prompt
      // rides along so the drafting chat can show it as the first user turn.
      const draft = await generateDraft(prompt);
      try {
        sessionStorage.setItem(
          AI_DRAFT_KEY,
          JSON.stringify({ ...draft, prompt }),
        );
      } catch {
        // sessionStorage unavailable (Safari private mode / quota). Navigate
        // anyway — NewDocView just opens an empty new-doc composer.
      }
      router.push("/app/wiki?new=1&ai=1");
    } catch (err) {
      setAiError(
        err instanceof Error ? err.message : "Couldn't generate a draft",
      );
      setGenerating(false);
    }
  }

  return (
    <main className={styles.view}>
      <div className={styles.scroll} ref={scrollRef}>
        <div className={styles.column}>
          {/* Hero */}
          <div className={styles.hero}>
            <span className={styles.heroMark}>
              <SvgOnyxLogo size={32} />
            </span>
            <h1 className={styles.heroTitle}>Welcome to Onyx Wiki</h1>
          </div>
          <div className={styles.dividerWrap}>
            <Divider />
          </div>

          <StartNewPage />

          {/* Write with AI */}
          <div className={styles.aiRow}>
            <span className={styles.aiGutterIcon}>
              {generating ? (
                <LoadingSpinner size={18} />
              ) : (
                <SvgOnyxOctagon size={18} />
              )}
            </span>
            <form className={styles.aiInputWrap} onSubmit={onAiSubmit}>
              <InputTypeIn
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                placeholder="Start writing with AI…"
                aria-label="Start writing with AI"
                rightChildren={
                  <Button
                    type="submit"
                    size="sm"
                    prominence="tertiary"
                    icon={SvgArrowUp}
                    disabled={!aiPrompt.trim() || generating}
                    aria-label="Start writing"
                  />
                }
              />
            </form>
          </div>
          {aiError && <p className={styles.aiError}>{aiError}</p>}

          <div className={styles.midDividerWrap}>
            <Divider />
          </div>

          {/* Recent Pages */}
          <div className={styles.sectionHeader}>
            <span className={styles.secHeadLg}>Recent Pages</span>
          </div>
          {recent.length === 0 ? (
            <p className={styles.empty}>
              No pages yet. Create one to get started.
            </p>
          ) : (
            <div className={styles.recentGrid}>
              {recent.map((p) => (
                <div key={p.path} className={styles.recentCell}>
                  <RecentCard
                    page={p}
                    onClick={() =>
                      router.push(p.id ? wikiHref(p.id) : wikiPath(p.path))
                    }
                  />
                </div>
              ))}
            </div>
          )}
          {/* Wiki-wide chat, last child of the column so it shares its
              exact box (mock 2361:65086). Chat-only: nothing is in view
              to attach, and watching or launching needs a scope. */}
          <WikiToolbarDock
            tabs={["chat"]}
            variant="column"
            defaultFolded={false}
            surface="home"
          />
        </div>
      </div>
      {/* Same thumb as the doc surfaces — native bar hidden in the module
          CSS, one scroll indicator everywhere. */}
      <EdgeScrollbar
        targetRef={scrollTarget}
        className="absolute inset-y-1 right-0 w-3"
      />
    </main>
  );
}

function RecentCard({
  page,
  onClick,
}: {
  page: RecentPage;
  onClick: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <SelectCard
      state="empty"
      onClick={onClick}
      padding="fit"
      rounding="lg"
      border="solid"
    >
      <div className={styles.recentInner}>
        <div className={styles.cardHead}>
          <Text font="main-ui-action" color="text-04" nowrap>
            {page.title}
          </Text>
          <div className={styles.cardPreview}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}>
              {page.preview}
            </ReactMarkdown>
          </div>
        </div>
        <Divider />
        <div className={styles.cardFoot}>
          <Text font="secondary-body" color="text-03" nowrap>
            {page.updated_at
              ? `Updated ${relativeTime(page.updated_at, "long")}`
              : "—"}
          </Text>
          <WikiItemMenu
            path={page.path}
            isFolder={false}
            open={menuOpen}
            onOpenChange={setMenuOpen}
            align="end"
          >
            {/* Plain span trigger — OPAL Button doesn't forward ref/onClick
                into Popover.Trigger asChild; the span anchors the popover and
                stops the click from navigating the card. */}
            <span
              className={styles.menuTrigger}
              onClick={(e) => e.stopPropagation()}
            >
              <Button
                size="sm"
                prominence="tertiary"
                icon={SvgMoreHorizontal}
                aria-label="More"
              />
            </span>
          </WikiItemMenu>
        </div>
      </div>
    </SelectCard>
  );
}
