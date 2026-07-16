"use client";

import { useRouter } from "next/navigation";
import useSWR from "swr";
import { LineItemButton, SelectCard, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgChevronRight, SvgPlusCircle } from "@onyx-ai/opal/icons";
import { listTemplateSummaries } from "@/lib/templates";
import type { DocumentTemplateSummary } from "@/lib/templates";
import styles from "@/components/wiki/StartNewPage.module.css";

/**
 * "Start a new page" section (mock 709:259993): a Blank card + the first two
 * featured templates + a More Templates row. Shared by the Home landing and
 * folder pages; `dir` scopes where the new page is created ("" = wiki root).
 */
export function StartNewPage({ dir = "" }: { dir?: string }) {
  const router = useRouter();
  const { data: templates } = useSWR<DocumentTemplateSummary[]>(
    "templates:summaries",
    listTemplateSummaries,
  );
  // The "Blank" template is the empty-start entry point (carries an
  // auto-update policy). Route the blank card through it when present, and
  // keep it out of the featured row so it isn't shown twice.
  const blankTemplate = (templates ?? []).find((t) => t.name === "Blank");
  const featured = (templates ?? [])
    .filter((t) => t.name !== "Blank")
    .slice(0, 2);

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
            glyph={
              <span className={styles.blankGlyph}>
                <SvgPlusCircle size={22} />
              </span>
            }
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
      <div className={styles.moreRow}>
        <LineItemButton
          variant="body"
          sizePreset="main-ui"
          title="More Templates"
          width="full"
          rightChildren={
            <SvgChevronRight size={18} className={styles.moreChevron} />
          }
          onClick={() => startNewPage()}
        />
      </div>
    </>
  );
}

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
