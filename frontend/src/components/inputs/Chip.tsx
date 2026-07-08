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
  /** Cap the label at 120px with an ellipsis (the mock's mini-tag form). */
  truncateLabel?: boolean;
}

/** Port of Onyx's refresh-components Chip (chip/tag with optional remove),
 * adapted to agent-wiki's Opal Text API and to the Figma Tag values (dark
 * text-04 label, tight padding). Swap to the library version when it
 * ships in @onyx-ai/opal. */
export default function Chip({
  children,
  icon: Icon,
  onRemove,
  smallLabel = false,
  error = false,
  truncateLabel = false,
}: ChipProps) {
  return (
    <div className="flex items-center gap-0.5 rounded-(--radius-08) bg-(--background-tint-02) px-1 py-0.5">
      {Icon && <Icon className="size-3 shrink-0 text-(--text-03)" />}
      {children && (
        <span
          className={
            truncateLabel
              ? "flex max-w-[120px] items-center truncate"
              : "flex items-center"
          }
        >
          <Text
            font={smallLabel ? "secondary-body" : "main-ui-body"}
            color="text-04"
            nowrap
            maxLines={1}
          >
            {children}
          </Text>
        </span>
      )}
      {error && (
        <SvgAlertTriangle className="size-3.5 shrink-0 text-(--status-warning-05)" />
      )}
      {onRemove && (
        <span className="flex items-center [&_svg]:text-(--text-05)">
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
        </span>
      )}
    </div>
  );
}
