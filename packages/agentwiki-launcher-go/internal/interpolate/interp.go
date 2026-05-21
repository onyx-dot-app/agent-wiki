// Package interpolate substitutes ${var} tokens against a typed context.
// Unknown variables raise; nil-valued variables raise too — manifests
// must declare the substitutions they need.
package interpolate

import (
	"fmt"
	"regexp"
	"strings"
)

type Context struct {
	Token          string
	Endpoint       string
	SessionID      string
	CliSessionID   string // empty for first-turn
	WorkingDir     string
	FirstTurnPrompt string // empty unless explicitly threaded
	PromptFilePath string
	McpConfigPath  string
	Home           string
	Dirhash        string
}

var re = regexp.MustCompile(`\$\{([a-z_]+)\}`)

func (c *Context) lookup(key string) (string, bool) {
	switch key {
	case "token":
		return c.Token, c.Token != ""
	case "endpoint":
		return c.Endpoint, c.Endpoint != ""
	case "session_id":
		return c.SessionID, c.SessionID != ""
	case "cli_session_id":
		return c.CliSessionID, c.CliSessionID != ""
	case "working_dir":
		return c.WorkingDir, c.WorkingDir != ""
	case "first_turn_prompt":
		return c.FirstTurnPrompt, c.FirstTurnPrompt != ""
	case "prompt_file_path":
		return c.PromptFilePath, c.PromptFilePath != ""
	case "mcp_config_path":
		return c.McpConfigPath, c.McpConfigPath != ""
	case "home":
		return c.Home, c.Home != ""
	case "dirhash":
		return c.Dirhash, c.Dirhash != ""
	}
	return "", false
}

func String(s string, ctx *Context) (string, error) {
	var firstErr error
	out := re.ReplaceAllStringFunc(s, func(match string) string {
		key := match[2 : len(match)-1]
		v, ok := ctx.lookup(key)
		if !ok {
			if firstErr == nil {
				firstErr = fmt.Errorf("missing interpolation value for ${%s}", key)
			}
			return match
		}
		return v
	})
	if firstErr != nil {
		return "", firstErr
	}
	return out, nil
}

func Argv(argv []string, ctx *Context) ([]string, error) {
	out := make([]string, 0, len(argv))
	for i, a := range argv {
		v, err := String(a, ctx)
		if err != nil {
			return nil, fmt.Errorf("argv[%d]: %w", i, err)
		}
		out = append(out, v)
	}
	return out, nil
}

func Env(env map[string]string, ctx *Context) (map[string]string, error) {
	out := make(map[string]string, len(env))
	for k, v := range env {
		s, err := String(v, ctx)
		if err != nil {
			return nil, fmt.Errorf("env.%s: %w", k, err)
		}
		out[k] = s
	}
	return out, nil
}

// unused but kept for symmetry; suppresses go vet warnings.
var _ = strings.TrimSpace
