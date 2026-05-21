package trust

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const (
	codexMcpStart = "# >>> agentwiki-launcher-managed (do not edit by hand)"
	codexMcpEnd   = "# <<< agentwiki-launcher-managed"
)

var agentwikiTableRE = regexp.MustCompile(`^\[mcp_servers\.agent-wiki(?:\.[^\]]+)?\]\s*$`)

// WriteCodexAgentwikiMcp ensures ~/.codex/config.toml has a managed
// agent-wiki MCP block with bearer_token_env_var = "AGENTWIKI_MCP_TOKEN".
// Prior managed blocks and any orphan agent-wiki tables are stripped
// first so codex doesn't see duplicate keys.
func WriteCodexAgentwikiMcp(url string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	path := filepath.Join(home, ".codex", "config.toml")
	raw, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	stripped := stripCodexBlocks(string(raw))
	block := renderCodexBlock(url)
	out := stripped
	if out != "" && !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	out += block
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp := fmt.Sprintf("%s.agw-tmp-%d", path, os.Getpid())
	if err := os.WriteFile(tmp, []byte(out), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func stripCodexBlocks(raw string) string {
	lines := strings.Split(raw, "\n")
	out := make([]string, 0, len(lines))
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if line == codexMcpStart || line == codexMcpEnd {
			continue
		}
		if agentwikiTableRE.MatchString(line) {
			i++
			for i < len(lines) && !strings.HasPrefix(lines[i], "[") {
				i++
			}
			i--
			continue
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}

func renderCodexBlock(url string) string {
	urlLit := tomlString(url)
	return codexMcpStart + "\n" +
		"[mcp_servers.agent-wiki]\n" +
		"url = " + urlLit + "\n" +
		`bearer_token_env_var = "AGENTWIKI_MCP_TOKEN"` + "\n" +
		codexMcpEnd + "\n"
}

// tomlString is duplicated from mcpconfig to avoid an import cycle.
func tomlString(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return `"` + s + `"`
}
