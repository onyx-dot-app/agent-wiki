import { Button, Text, Tooltip } from "@onyx-ai/opal/components";
import { cn } from "@onyx-ai/opal/utils";
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
  /** Hover tooltip content. Defaults to the label. */
  tooltip?: string;
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
  tooltip,
}: ChipProps) {
  const tooltipText = tooltip ?? children;
  return (
    <Tooltip
      tooltip={
        tooltipText ? (
          <span className="font-secondary-body break-all">{tooltipText}</span>
        ) : undefined
      }
      side="top"
    >
      <div
        className={cn(
          "flex max-w-full min-w-0 items-center bg-(--background-tint-02)",
          smallLabel
            ? "gap-0 rounded-(--radius-04) p-[2px]"
            : "gap-0.5 rounded-(--radius-08) px-1 py-0.5",
        )}
      >
        {Icon && <Icon className="size-3 shrink-0 text-(--text-03)" />}
        {children && (
          // leading-[0]: kill the wrapper's line-box strut so the inner Text's
          // line-height sizes it and flex centers glyphs with the remove button.
          <span
            className={cn(
              "block min-w-0 truncate leading-[0]",
              truncateLabel && "max-w-[120px]",
            )}
          >
            <Text
              font={smallLabel ? "figure-small-value" : "main-ui-body"}
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
          <span
            className={cn(
              "flex items-center",
              smallLabel
                ? "[&_button]:!size-3 [&_svg]:!size-2.5 [&_svg]:text-(--text-04)"
                : "[&_svg]:text-(--text-05)",
            )}
          >
            <Button
              type="button"
              prominence="tertiary"
              icon={SvgX}
              size={smallLabel ? "fit" : "xs"}
              onClick={(e) => {
                e.stopPropagation();
                onRemove();
              }}
            />
          </span>
        )}
      </div>
    </Tooltip>
  );
}
