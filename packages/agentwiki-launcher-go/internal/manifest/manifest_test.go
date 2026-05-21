package manifest

import (
	"encoding/json"
	"strings"
	"testing"
)

func base() map[string]any {
	return map[string]any{
		"manifest_version":    1,
		"id":                  "x",
		"name":                "X",
		"tagline":             "t",
		"icon_url":            "/i.svg",
		"kind":                "local_cli",
		"mcp_config_format":   "claude_json",
		"first_turn_prompt_delivery": map[string]any{"method": "positional_arg"},
		"launch": map[string]any{
			"binary":                "claude",
			"argv":                  []string{"--mcp-config", "${mcp_config_path}"},
			"env":                   map[string]string{"S": "${session_id}"},
			"cwd":                   "${working_dir}",
			"unscoped_workdir_argv": []string{"--permission-mode", "bypassPermissions"},
		},
	}
}

func parseBytes(t *testing.T, m map[string]any) (*Manifest, error) {
	t.Helper()
	b, _ := json.Marshal(m)
	return Parse(b)
}

func TestParseAcceptsValid(t *testing.T) {
	m, err := parseBytes(t, base())
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if m.ID != "x" || m.Launch.Binary != "claude" {
		t.Errorf("unexpected: %+v", m)
	}
}

func TestParseRejectsTokenInArgv(t *testing.T) {
	m := base()
	m["launch"].(map[string]any)["argv"] = []string{"--token", "${token}"}
	_, err := parseBytes(t, m)
	if err == nil || !strings.Contains(err.Error(), "${token}") {
		t.Errorf("expected ${token} rejection, got %v", err)
	}
}

func TestParseRejectsFirstTurnPromptToken(t *testing.T) {
	m := base()
	m["launch"].(map[string]any)["argv"] = []string{"${first_turn_prompt}"}
	_, err := parseBytes(t, m)
	if err == nil || !strings.Contains(err.Error(), "${first_turn_prompt}") {
		t.Errorf("expected first_turn_prompt rejection, got %v", err)
	}
}

func TestParseRejectsInterpolationInUnscopedArgv(t *testing.T) {
	m := base()
	m["launch"].(map[string]any)["unscoped_workdir_argv"] = []string{"${working_dir}"}
	_, err := parseBytes(t, m)
	if err == nil || !strings.Contains(err.Error(), "unscoped_workdir_argv") {
		t.Errorf("expected unscoped_workdir_argv rejection, got %v", err)
	}
}

func TestParseRejectsUnknownVar(t *testing.T) {
	m := base()
	m["launch"].(map[string]any)["argv"] = []string{"${not_a_real_var}"}
	_, err := parseBytes(t, m)
	if err == nil {
		t.Error("expected unknown-var rejection")
	}
}

