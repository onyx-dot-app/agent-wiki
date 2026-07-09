import Chip from "@/components/inputs/Chip";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

export interface ChipListItem {
  /** Stable React key and the value handed back to `onRemove`. */
  id: string;
  /** Text shown inside the chip. */
  label: string;
}

/** A wrap-flowing row of removable {@link Chip}s (small, 120px truncation)
 * for any labelled list. All items show by default. `maxVisible` collapses
 * the rest into a "+N" chip (with `overflowIcon`) whose tooltip names the
 * hidden entries. */
export default function ChipList({
  items,
  onRemove,
  maxVisible,
  overflowIcon,
}: {
  items: ChipListItem[];
  onRemove?: (id: string) => void;
  maxVisible?: number;
  overflowIcon?: IconFunctionComponent;
}) {
  const visible = maxVisible === undefined ? items : items.slice(0, maxVisible);
  const hidden = items.slice(visible.length);
  return (
    <div className="flex w-full flex-wrap items-center gap-1">
      {visible.map((item) => (
        <Chip
          key={item.id}
          smallLabel
          truncateLabel
          onRemove={onRemove ? () => onRemove(item.id) : undefined}
        >
          {item.label}
        </Chip>
      ))}
      {hidden.length > 0 && (
        <Chip
          smallLabel
          icon={overflowIcon}
          tooltip={hidden.map((item) => item.label).join(", ")}
        >
          {`+${hidden.length}`}
        </Chip>
      )}
    </div>
  );
}
