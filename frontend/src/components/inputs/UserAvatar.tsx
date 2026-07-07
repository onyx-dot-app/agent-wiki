import { Text } from "@onyx-ai/opal/components";
import { SvgUser } from "@onyx-ai/opal/icons";

export interface UserAvatarProps {
  /** Display name or email; the first character becomes the initial. */
  name?: string | null;
  size?: number;
}

/** Port of Onyx's refresh-components UserAvatar (initial-in-circle, icon
 * fallback), decoupled from the Onyx User type: callers pass the display
 * name. Swap to the library version when it ships in @onyx-ai/opal. */
export default function UserAvatar({ name, size = 20 }: UserAvatarProps) {
  const initial = name?.trim()[0]?.toUpperCase();

  if (!initial) {
    return (
      <div
        role="img"
        aria-label="avatar"
        className="flex items-center justify-center rounded-full bg-(--background-tint-01)"
        style={{ width: size, height: size }}
      >
        <SvgUser
          aria-hidden
          className="text-(--text-03)"
          style={{ width: size * 0.55, height: size * 0.55 }}
        />
      </div>
    );
  }

  return (
    <div
      role="img"
      aria-label={`${name} avatar`}
      className="flex items-center justify-center rounded-full border border-(--border-01) bg-(--background-neutral-inverted-00)"
      style={{ width: size, height: size }}
    >
      <Text font="secondary-action" color="text-inverted-05">
        {initial}
      </Text>
    </div>
  );
}
