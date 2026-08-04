"use client";

// Model picker body, structured after onyx's ModelSelectorContent: search on
// top, provider groups that collapse, select-heavy rows with a check on the
// active model.
import { useState, useMemo, useRef, useEffect } from "react";

import {
  Button,
  Card,
  LineItemButton,
  Text,
  InputTypeIn,
  PopoverMenu,
} from "@onyx-ai/opal/components";
import { SvgCheck, SvgChevronRight } from "@onyx-ai/opal/icons";
import { ContentAction, Section } from "@onyx-ai/opal/layouts";
import { cn } from "@onyx-ai/opal/utils";
import { Interactive } from "@onyx-ai/opal/core";

import {
  type AvailableProvider,
  type LLMOption,
  buildLlmOptions,
  groupLlmOptions,
  llmOptionKey,
} from "@/lib/llmOptions";

export interface ModelSelectorContentProps {
  providers: AvailableProvider[] | undefined;
  isLoading?: boolean;
  onSelect: (option: LLMOption) => void;
  isSelected: (option: LLMOption) => boolean;
  scrollContainerRef?: React.RefObject<HTMLDivElement | null>;
  footer?: React.ReactNode;
}

export default function ModelSelectorContent({
  providers,
  isLoading = false,
  onSelect,
  isSelected,
  scrollContainerRef: externalScrollRef,
  footer,
}: ModelSelectorContentProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const internalScrollRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = externalScrollRef ?? internalScrollRef;

  const llmOptions = useMemo(() => buildLlmOptions(providers), [providers]);

  const filteredOptions = useMemo(() => {
    if (!searchQuery.trim()) return llmOptions;
    const query = searchQuery.toLowerCase();
    return llmOptions.filter(
      (opt) =>
        opt.displayName.toLowerCase().includes(query) ||
        opt.modelName.toLowerCase().includes(query) ||
        opt.providerDisplayName.toLowerCase().includes(query),
    );
  }, [llmOptions, searchQuery]);

  const groupedOptions = useMemo(
    () => groupLlmOptions(filteredOptions),
    [filteredOptions],
  );

  const defaultGroupKey = useMemo(() => {
    for (const group of groupedOptions) {
      if (group.options.some((opt) => isSelected(opt))) {
        return group.key;
      }
    }
    return groupedOptions[0]?.key ?? "";
  }, [groupedOptions, isSelected]);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set([defaultGroupKey]),
  );

  useEffect(() => {
    setExpandedGroups(new Set([defaultGroupKey]));
  }, [defaultGroupKey]);

  const isSearching = searchQuery.trim().length > 0;

  const toggleGroup = (key: string) => {
    if (isSearching) return;
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isGroupOpen = (key: string) => isSearching || expandedGroups.has(key);

  const renderModelItem = (option: LLMOption) => {
    const selected = isSelected(option);

    return (
      <LineItemButton
        key={llmOptionKey(option)}
        selectVariant="select-heavy"
        state={selected ? "selected" : "empty"}
        icon={(props) => <div className={props.className} />}
        title={option.displayName}
        onClick={() => onSelect(option)}
        rightChildren={
          selected ? (
            // raw-ok: icon-height alignment box, matches the onyx selector row.
            <div className="flex h-5 items-center">
              <SvgCheck className="text-action-link-05" size={16} />
            </div>
          ) : null
        }
        sizePreset="main-ui"
        rounding="sm"
      />
    );
  };

  return (
    <Section gap={0.5}>
      <InputTypeIn
        searchIcon
        variant="internal"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder="Search models..."
      />

      <PopoverMenu scrollContainerRef={scrollContainerRef}>
        {isLoading
          ? [
              <Text key="loading" font="secondary-body" color="text-03">
                Loading models...
              </Text>,
            ]
          : groupedOptions.length === 0
            ? [
                <Text key="empty" font="secondary-body" color="text-03">
                  No models found
                </Text>,
              ]
            : groupedOptions.length === 1
              ? [
                  <Section
                    key="single-provider"
                    gap={0.25}
                    alignItems="stretch"
                  >
                    {groupedOptions[0]!.options.map(renderModelItem)}
                  </Section>,
                ]
              : groupedOptions.map((group) => {
                  const open = isGroupOpen(group.key);
                  return (
                    <Card
                      key={group.key}
                      expandable
                      expanded={open}
                      expandableContentHeight="fit"
                      background="none"
                      padding="fit"
                      expandedContent={
                        <Section gap={0.25} alignItems="stretch">
                          {group.options.map(renderModelItem)}
                        </Section>
                      }
                    >
                      <Interactive.Stateless prominence="tertiary">
                        <Interactive.Container
                          size="fit"
                          rounding="sm"
                          width="full"
                        >
                          {/* raw-ok: asymmetric row inset from the onyx selector header. */}
                          <div
                            className="w-full py-1 pr-1 pl-2"
                            onClick={() => toggleGroup(group.key)}
                          >
                            <ContentAction
                              sizePreset="secondary"
                              variant="body"
                              color="muted"
                              icon={group.Icon}
                              title={group.displayName}
                              padding="fit"
                              rightChildren={
                                <Section>
                                  <Button
                                    icon={(props) => (
                                      <SvgChevronRight
                                        {...props}
                                        className={cn(
                                          "transition-all",
                                          open && "rotate-90",
                                          props.className,
                                        )}
                                      />
                                    )}
                                    prominence="tertiary"
                                    size="sm"
                                  />
                                </Section>
                              }
                              center
                            />
                          </div>
                        </Interactive.Container>
                      </Interactive.Stateless>
                    </Card>
                  );
                })}
      </PopoverMenu>

      {footer}
    </Section>
  );
}
