#!/usr/bin/env bash
# Fails when a raw interactive element is ADDED in frontend/src. The design
# system (@onyx-ai/opal) provides Button, SelectButton, InputTypeIn,
# FilterButton, Text, etc. New raw <button>, <input>, <select>, or <textarea>
# needs a `raw-ok: <reason>` comment on the same or preceding line naming a
# real library gap. Pre-existing debt is pinned per file in
# raw-elements-baseline.txt (count may shrink, never grow); files not listed
# there must be clean.
set -euo pipefail

baseline="$(dirname "$0")/raw-elements-baseline.txt"

count_unmarked() {
  local f=$1 n=0 ln line prev
  while IFS=: read -r ln _; do
    line=$(sed -n "${ln}p" "$f")
    prev=$(sed -n "$((ln - 1))p" "$f")
    if [[ "$line" != *"raw-ok:"* && "$prev" != *"raw-ok:"* ]]; then
      n=$((n + 1))
    fi
  done < <(grep -nE '<(button|input|select|textarea)([ >]|$)' "$f" || true)
  echo "$n"
}

fail=0
for f in "$@"; do
  [ -f "$f" ] || continue
  rel=${f#./}
  n=$(count_unmarked "$f")
  # exact-match lookup — route paths contain regex metacharacters
  allowed=$(awk -v f="$rel" '$1 == f { print $2 }' "$baseline" 2>/dev/null || true)
  allowed=${allowed:-0}
  if [ "$n" -gt "$allowed" ]; then
    echo "$rel: $n raw interactive element(s) without 'raw-ok:' justification (baseline allows $allowed)"
    echo "    use the Opal component (Button/SelectButton/InputTypeIn/FilterButton/Text/...)"
    echo "    or, for a real library gap, add {/* raw-ok: <reason> */} on the preceding line"
    fail=1
  fi
done
exit $fail
