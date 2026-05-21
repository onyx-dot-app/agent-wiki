package trust

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// MarkCodexProjectTrusted appends
//
//	[projects."<cwd>"]
//	trust_level = "trusted"
//
// to ~/.codex/config.toml if not already present.
func MarkCodexProjectTrusted(cwd string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	path := filepath.Join(home, ".codex", "config.toml")
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	header := fmt.Sprintf(`[projects.%q]`, cwd)
	re, err := regexp.Compile(`(?m)^` + regexp.QuoteMeta(header) + `\b`)
	if err != nil {
		return err
	}
	if re.Match(raw) {
		return nil
	}
	block := "\n" + header + "\ntrust_level = \"trusted\"\n"
	out := string(raw)
	if !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	out += block
	tmp := fmt.Sprintf("%s.agw-tmp-%d", path, os.Getpid())
	if err := os.WriteFile(tmp, []byte(out), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
