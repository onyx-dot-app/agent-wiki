/** Strip the directory prefix and `.md` extension from a wiki path to get the
 * human-readable page title. Works on full paths (`"Foo/Bar/My Doc.md"`) and
 * bare basenames (`"My Doc.md"`). */
export function pageTitle(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/i, "");
}
