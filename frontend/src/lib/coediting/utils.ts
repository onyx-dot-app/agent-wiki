import type { CoeditChange } from "./types";

/** Diff `oldStr` → `newStr` into one range change (trim common prefix/suffix),
 * or null if unchanged. Offsets are UTF-16 code units (JS-native), matching the
 * server. Coarse (one span), which is all the server needs. */
export function diffToChange(
  oldStr: string,
  newStr: string,
): CoeditChange | null {
  if (oldStr === newStr) return null;
  const oldLen = oldStr.length;
  const newLen = newStr.length;
  const maxPre = Math.min(oldLen, newLen);
  let pre = 0;
  while (pre < maxPre && oldStr.charCodeAt(pre) === newStr.charCodeAt(pre))
    pre++;
  const maxSuf = Math.min(oldLen, newLen) - pre;
  let suf = 0;
  while (
    suf < maxSuf &&
    oldStr.charCodeAt(oldLen - 1 - suf) === newStr.charCodeAt(newLen - 1 - suf)
  ) {
    suf++;
  }
  return {
    from: pre,
    to: oldLen - suf,
    insert: newStr.slice(pre, newLen - suf),
  };
}

/** Apply a range change to a string (UTF-16 offsets). */
export function applyChange(str: string, c: CoeditChange): string {
  return str.slice(0, c.from) + c.insert + str.slice(c.to);
}
