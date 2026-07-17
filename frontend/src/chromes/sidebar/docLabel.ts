/** Display label for a wiki doc path in the sidebar: filename without
 * the .md extension. Shared by the Starred and Recents sections. */
export function docLabel(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/, "");
}
