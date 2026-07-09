"use client";

import { Tag } from "@onyx-ai/opal/components";

import UserAvatar from "@/components/inputs/UserAvatar";
import {
  SvgActivity,
  SvgBook,
  SvgFile,
  SvgFolder,
  SvgLink,
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
  if (type === "webhook") return SvgLink;
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

/** Overlapping 20px owner avatar + destination-type circles. The owner
 * circle is the ported UserAvatar; destination circles stay custom (no
 * icon-in-circle primitive exists in Opal or refresh-components). */
export function AvatarCluster({
  ownerName,
  destinationTypes,
}: {
  ownerName: string;
  destinationTypes: string[];
}) {
  return (
    <span className="flex items-center px-[2px]">
      <UserAvatar name={ownerName} size={20} />
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
