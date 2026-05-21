// Package trust pre-marks per-folder trust in claude / codex configs
// so the launched agent doesn't block on the trust dialog. Only invoked
// when the helper launches in unscoped (no working_dir) mode + the
// manifest declared an unscoped_workdir_argv set.
package trust

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// MarkClaudeWorkspaceTrusted flips ``projects.<cwd>.hasTrustDialogAccepted``
// to true in ``~/.claude.json``. Atomic write via tmp + rename.
func MarkClaudeWorkspaceTrusted(cwd string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	path := filepath.Join(home, ".claude.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	var cfg map[string]any
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return fmt.Errorf("parse ~/.claude.json: %w", err)
	}
	projects, _ := cfg["projects"].(map[string]any)
	if projects == nil {
		projects = map[string]any{}
		cfg["projects"] = projects
	}
	proj, _ := projects[cwd].(map[string]any)
	if proj == nil {
		proj = map[string]any{}
		projects[cwd] = proj
	}
	if v, ok := proj["hasTrustDialogAccepted"].(bool); ok && v {
		return nil
	}
	proj["hasTrustDialogAccepted"] = true
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := fmt.Sprintf("%s.agw-tmp-%d", path, os.Getpid())
	if err := os.WriteFile(tmp, out, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
