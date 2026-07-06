#!/usr/bin/env bash
# Fails when a raw interactive element appears in frontend/src without an
# explicit justification. The design system (@onyx-ai/opal) provides Button,
# SelectButton, InputTypeIn, FilterButton, Text, etc. — raw <button>, <input>,
# <select>, and <textarea> are only allowed with a `raw-ok: <reason>` comment
# on the same or the preceding line (e.g. {/* raw-ok: no Opal multiline input */}).
set -euo pipefail

fail=0
for f in "$@"; do
  [ -f "$f" ] || continue
  while IFS=: read -r ln _; do
    line=$(sed -n "${ln}p" "$f")
    prev=$(sed -n "$((ln - 1))p" "$f")
    if [[ "$line" != *"raw-ok:"* && "$prev" != *"raw-ok:"* ]]; then
      echo "$f:$ln: raw interactive element without 'raw-ok:' justification"
      echo "    use the Opal component (Button/SelectButton/InputTypeIn/...) or add {/* raw-ok: <reason> */}"
      fail=1
    fi
  done < <(grep -nE '<(button|input|select|textarea)([ >]|$)' "$f" || true)
done
exit $fail
