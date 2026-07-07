"use client";

import { Tag, Text } from "@onyx-ai/opal/components";
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

/** The page/folder tag shared by trigger cards and activity rows. The
 * wrapper span only bounds width for long paths and carries the full-path
 * hover title. */
export function ScopeChip({ scope }: { scope: string }) {
  return (
    <span
      className="flex max-w-[220px] items-center overflow-hidden"
      title={scope || "Whole wiki"}
    >
      <Tag
        icon={scopeIcon(scope)}
        title={scope ? formatScopePath(scope).replace(/^\//, "") : "Whole wiki"}
      />
    </span>
  );
}

/** Overlapping 20px owner-initial + destination-type circles. Custom spans:
 * Opal ships no avatar primitive (checked alias-tolerant across the dist —
 * only Table's qualifier-column docs mention avatars). */
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
