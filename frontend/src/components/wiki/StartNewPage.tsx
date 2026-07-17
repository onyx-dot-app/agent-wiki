"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import {
  IconContainer,
  LineItemButton,
  SelectCard,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgChevronRight, SvgPlus, SvgSquare } from "@onyx-ai/opal/icons";
import { cn } from "@onyx-ai/opal/utils";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { listTemplateSummaries } from "@/lib/templates";
import type { DocumentTemplateSummary } from "@/lib/templates";
import styles from "@/components/wiki/StartNewPage.module.css";

/**
 * "Start a new page" section (mock 709:259993): a Blank card + the first two
 * featured templates, with a More Templates row that expands the full gallery
 * in place (mock 709:260311). Shared by the Home landing and folder pages.
 * `dir` scopes where the new page is created ("" = wiki root).
 */
export function StartNewPage({ dir = "" }: { dir?: string }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const { data: templates } = useSWR<DocumentTemplateSummary[]>(
    "templates:summaries",
    listTemplateSummaries,
  );
  // The "Blank" template is the empty-start entry point (carries an
  // auto-update policy). Route the blank card through it when present, and
  // keep it out of the featured row so it isn't shown twice.
  const all = templates ?? [];
  const blankTemplate = all.find((t) => t.name === "Blank");
  const nonBlank = all.filter((t) => t.name !== "Blank");
  const featured = expanded ? nonBlank : nonBlank.slice(0, 2);
  const hasMore = !expanded && nonBlank.length > 2;

  function startNewPage(templateId?: string) {
    const qs = templateId
      ? `?new=1&template=${encodeURIComponent(templateId)}`
      : "?new=1";
    router.push(dir ? `/app/wiki/${dir}${qs}` : `/app/wiki${qs}`);
  }

  return (
    <>
      <div className={styles.sectionHeader}>
        <span className={styles.secHead}>Start a new page</span>
      </div>
      <div className={styles.templates}>
        <div className={styles.templateCell}>
          <TemplateCard
            title="Blank page"
            glyph={<IconContainer size="main-ui" icon={SquarePlusGlyph} />}
            onClick={() => startNewPage(blankTemplate?.id)}
          />
        </div>
        {featured.map((t) => (
          <div key={t.id} className={styles.templateCell}>
            <TemplateCard
              title={t.name}
              description={t.description ?? ""}
              note={
                t.ingestion_auto_update_disabled ? "Auto-update off" : undefined
              }
              onClick={() => startNewPage(t.id)}
            />
          </div>
        ))}
      </div>
      {hasMore && (
        <div className={styles.moreRow}>
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            title="More Templates"
            width="full"
            rightChildren={
              <SvgChevronRight size={18} className={styles.moreChevron} />
            }
            onClick={() => setExpanded(true)}
          />
        </div>
      )}
    </>
  );
}

// Square-plus layered from published icons, sized by IconContainer's glyph
// class. Swap to Opal's SvgSquarePlus when it ships (onyx #13153).
const SquarePlusGlyph: IconFunctionComponent = ({ className }) => (
  <span className={cn(styles.glyphStack, className)}>
    <SvgSquare size={16} />
    <span className={styles.glyphPlus}>
      <SvgPlus size={8} />
    </span>
  </span>
);

function TemplateCard({
  title,
  description,
  note,
  glyph,
  onClick,
}: {
  title: string;
  description?: string;
  note?: string;
  glyph?: React.ReactNode;
  onClick: () => void;
}) {
  return (
    // Mock card chrome (709:259996): tint fill, radius 12 (default rounding),
    // no border. Filled is the select-card variant's visible-background rest.
    <SelectCard state="filled" onClick={onClick} padding="sm" border="none">
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={glyph ? 0.75 : 0.25}
        width="full"
        height="full"
      >
        <Text font="main-ui-action" color="text-03" nowrap>
          {title}
        </Text>
        {glyph ? (
          glyph
        ) : (
          <>
            {description ? (
              <Text font="secondary-body" color="text-03">
                {description}
              </Text>
            ) : null}
            {note ? (
              <Text font="secondary-body" color="text-02">
                {note}
              </Text>
            ) : null}
          </>
        )}
      </Section>
    </SelectCard>
  );
}
