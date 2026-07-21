import { InputTypeIn } from "@onyx-ai/opal/components";

/** Borderless search field over a panel card tint (mock 1912:355461 /
 *  1899:296190), InputTypeIn's chromeless internal variant. */
export function PanelSearchField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    // The 14px override lives in globals.css (.panel-search).
    <div className="panel-search min-w-0 flex-1 p-[1px]">
      <InputTypeIn
        variant="internal"
        searchIcon
        placeholder={placeholder}
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
