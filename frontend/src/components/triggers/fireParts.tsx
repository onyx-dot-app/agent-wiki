"use client";

import { Text } from "@onyx-ai/opal/components";
import {
  SvgActivity,
  SvgBook,
  SvgFile,
  SvgFolder,
  SvgMail,
  SvgSlack,
} from "@onyx-ai/opal/icons";

import { formatScopePath } from "@/lib/format";

export function scopeIcon(scope: string) {
  if (scope === "/" || scope === "") return SvgBook;
  return scope.endsWith(".md") ? SvgFile : SvgFolder;
}

export function destinationIcon(type: string) {
  if (type === "slack") return SvgSlack;
  if (type === "email") return SvgMail;
  return SvgActivity;
}

/** The tinted page/folder tag shared by trigger cards and activity rows. */
export function ScopeChip({ scope }: { scope: string }) {
  const Icon = scopeIcon(scope);
  return (
    <span
      className="flex max-w-[220px] items-center rounded-(--radius-04) bg-(--background-tint-02) p-[2px]"
      title={scope || "Whole wiki"}
    >
      <span className="flex size-4 items-center justify-center p-[2px]">
        <Icon className="size-3 text-(--text-03)" />
      </span>
      <span className="min-w-0 px-[2px]">
        <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
          {scope ? formatScopePath(scope).replace(/^\//, "") : "Whole wiki"}
        </Text>
      </span>
    </span>
  );
}

/** Overlapping 20px owner-initial + destination-type circles. */
export function AvatarCluster({
  ownerName,
  destinationTypes,
}: {
  ownerName: string;
  destinationTypes: string[];
}) {
  return (
    <span className="flex items-center px-[2px]">
      <span className="flex size-5 items-center justify-center rounded-full border border-(--border-01) bg-(--background-neutral-inverted-00)">
        <Text font="secondary-action" color="text-inverted-05">
          {(ownerName[0] ?? "?").toUpperCase()}
        </Text>
      </span>
      {destinationTypes.map((type) => {
        const Icon = destinationIcon(type);
        return (
          <span
            key={type}
            className="-ml-1 flex size-5 items-center justify-center rounded-full border border-(--border-01) bg-(--background-neutral-00)"
          >
            <Icon className="size-3 text-(--text-04)" />
          </span>
        );
      })}
    </span>
  );
}
