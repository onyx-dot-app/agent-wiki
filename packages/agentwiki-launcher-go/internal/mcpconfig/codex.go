package mcpconfig

import (
	"fmt"
	"strings"
)

// RenderCodexToml returns a TOML snippet for codex's mcp_servers block.
// Standalone file form — useful for tests / a future codex
// `--mcp-config` flag. The live install path uses
// trust.WriteCodexAgentwikiMcp to write a marked block into
// ~/.codex/config.toml since codex has no `--mcp-config` flag today.
func RenderCodexToml(url, bearer string) string {
	var sb strings.Builder
	sb.WriteString("[mcp_servers.agent-wiki]\n")
	fmt.Fprintf(&sb, "url = %s\n", tomlString(url))
	sb.WriteString("[mcp_servers.agent-wiki.headers]\n")
	fmt.Fprintf(&sb, "Authorization = %s\n", tomlString("Bearer "+bearer))
	return sb.String()
}

func tomlString(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return `"` + s + `"`
}
