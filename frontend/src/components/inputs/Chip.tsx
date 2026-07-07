import { Button, Text } from "@onyx-ai/opal/components";
import { SvgAlertTriangle, SvgX } from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

export interface ChipProps {
  children?: string;
  icon?: IconFunctionComponent;
  onRemove?: () => void;
  smallLabel?: boolean;
  /** Warning-coloured indicator icon after the label. */
  error?: boolean;
}

/** Port of Onyx's refresh-components Chip (chip/tag with optional remove),
 * adapted to agent-wiki's Opal Text API. Swap to the library version when it
 * ships in @onyx-ai/opal. */
export default function Chip({
  children,
  icon: Icon,
  onRemove,
  smallLabel = true,
  error = false,
}: ChipProps) {
  return (
    <div className="flex items-center gap-1 rounded-(--radius-08) bg-(--background-tint-02) px-1.5 py-0.5">
      {Icon && <Icon className="size-3 shrink-0 text-(--text-03)" />}
      {children && (
        <Text
          font={smallLabel ? "secondary-body" : "main-ui-body"}
          color="text-03"
          nowrap
          maxLines={1}
        >
          {children}
        </Text>
      )}
      {error && (
        <SvgAlertTriangle className="size-3.5 shrink-0 text-(--status-warning-05)" />
      )}
      {onRemove && (
        <Button
          type="button"
          prominence="tertiary"
          icon={SvgX}
          size="xs"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        />
      )}
    </div>
  );
}
