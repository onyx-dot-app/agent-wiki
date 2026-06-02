/** Minimal rehype plugin: copy each element's source position into a
 * `data-sourcepos="L:C-L:C"` attribute.
 *
 * react-markdown v9 dropped the built-in `sourcePos` option, but the hast nodes
 * still carry `position` (mdast positions pass through remark-rehype). The
 * comment-anchor mapper (`selectionToAnchor`) reads `data-sourcepos` to map a
 * rendered selection back to raw-markdown offsets. Hand-rolled walk so we don't
 * add a `unist-util-visit` dependency.
 */

interface HastNode {
  type: string;
  position?: {
    start?: { line: number; column: number };
    end?: { line: number; column: number };
  };
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

export function rehypeSourcePos() {
  return (tree: HastNode): HastNode => {
    const walk = (node: HastNode): void => {
      const { start, end } = node.position ?? {};
      if (node.type === "element" && start && end) {
        node.properties = node.properties ?? {};
        // camelCase `dataSourcepos` is serialized to the `data-sourcepos`
        // attribute by property-information (react-markdown's attribute layer).
        node.properties.dataSourcepos = `${start.line}:${start.column}-${end.line}:${end.column}`;
      }
      node.children?.forEach(walk);
    };
    walk(tree);
    return tree;
  };
}
