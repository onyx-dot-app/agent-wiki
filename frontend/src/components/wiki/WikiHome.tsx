"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";

import {
  Button,
  Divider,
  InputTypeIn,
  SelectCard,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import {
  SvgArrowUp,
  SvgChevronRight,
  SvgFolder,
  SvgMoreHorizontal,
  SvgOnyxOctagon,
  SvgPlusCircle,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";

import { relativeTime } from "@/lib/time";
import { listTemplateSummaries } from "@/lib/templates";
import type { DocumentTemplateSummary } from "@/lib/templates";
import type { RecentPage } from "@/lib/wiki";

import { WikiTree } from "./WikiTree";
import styles from "./WikiHome.module.css";

// Stash key for the typed "write with AI" prompt; the new-page composer
// picks it up to seed the assistant.
const AI_PROMPT_KEY = "wiki:aiPrompt";

export function WikiHome() {
  const router = useRouter();
  const [aiPrompt, setAiPrompt] = useState("");

  const { data: recentData } = useSWR<{ pages: RecentPage[] }>(
    "/wiki/recent?limit=12",
  );
  const recent = recentData?.pages ?? [];

  const { data: templates } = useSWR<DocumentTemplateSummary[]>(
    "templates:summaries",
    listTemplateSummaries,
  );
  const featured = (templates ?? []).slice(0, 2);

  function startNewPage(templateId?: string) {
    const qs = templateId
      ? `?new=1&template=${encodeURIComponent(templateId)}`
      : "?new=1";
    router.push(`/app/wiki${qs}`);
  }

  function onAiSubmit(e: FormEvent) {
    e.preventDefault();
    const prompt = aiPrompt.trim();
    if (!prompt) return;
    try {
      sessionStorage.setItem(AI_PROMPT_KEY, prompt);
    } catch {
      // sessionStorage may be unavailable (private mode).
    }
    router.push("/app/wiki?new=1");
  }

  return (
    <main className={styles.scroll}>
      {/* Top header — breadcrumb (Home root; tree self-navigates by expanding) */}
      <div className={styles.topHeader}>
        <div className={styles.breadcrumb}>
          <span className={styles.modeBox}>
            <SvgFolder size={18} />
          </span>
          <span className={styles.crumb}>Home</span>
        </div>
      </div>

      <div className={styles.body}>
        <div className={styles.sidebar}>
          <WikiTree />
        </div>

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

          {/* Start a new page */}
          <div className={styles.sectionHeader}>
            <span className={styles.secHead}>Start a new page</span>
          </div>
          <div className={styles.templates}>
            <div className={styles.templateCell}>
              <TemplateCard
                title="Blank page"
                glyph={
                  <span className={styles.blankGlyph}>
                    <SvgPlusCircle size={22} />
                  </span>
                }
                onClick={() => startNewPage()}
              />
            </div>
            {featured.map((t) => (
              <div key={t.id} className={styles.templateCell}>
                <TemplateCard
                  title={t.name}
                  description={t.description ?? ""}
                  onClick={() => startNewPage(t.id)}
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            className={styles.moreRow}
            onClick={() => startNewPage()}
          >
            <span className={styles.moreLabel}>More Templates</span>
            <SvgChevronRight size={18} className={styles.moreChevron} />
          </button>

          {/* Write with AI */}
          <div className={styles.aiRow}>
            <span className={styles.aiGutterIcon}>
              <SvgOnyxOctagon size={18} />
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
                    disabled={!aiPrompt.trim()}
                    aria-label="Start writing"
                  />
                }
              />
            </form>
          </div>

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
                    onClick={() => router.push(`/app/wiki/${p.path}`)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function TemplateCard({
  title,
  description,
  glyph,
  onClick,
}: {
  title: string;
  description?: string;
  glyph?: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <SelectCard
      state="empty"
      onClick={onClick}
      padding="sm"
      rounding="lg"
      border="solid"
    >
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={0.25}
        width="full"
        height="full"
      >
        <Text font="main-ui-action" color="text-05" nowrap>
          {title}
        </Text>
        {glyph ? (
          glyph
        ) : description ? (
          <Text font="secondary-body" color="text-03">
            {description}
          </Text>
        ) : null}
      </Section>
    </SelectCard>
  );
}

function RecentCard({
  page,
  onClick,
}: {
  page: RecentPage;
  onClick: () => void;
}) {
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
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
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
          <Button
            size="sm"
            prominence="tertiary"
            icon={SvgMoreHorizontal}
            aria-label="More"
            onClick={(e) => {
              e.stopPropagation();
            }}
          />
        </div>
      </div>
    </SelectCard>
  );
}
