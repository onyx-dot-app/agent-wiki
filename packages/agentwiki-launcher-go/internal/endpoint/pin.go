// Package endpoint persists + verifies the wiki backend the helper is
// allowed to talk to. Defends against an attacker URL minting a fake
// launch_code URL — only the pinned endpoint is trusted.
package endpoint

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
)

func path() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".agentwiki", "endpoint.url"), nil
}

func Get() (string, error) {
	p, err := path()
	if err != nil {
		return "", err
	}
	b, err := os.ReadFile(p)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", nil
		}
		return "", err
	}
	return strings.TrimSpace(string(b)), nil
}

func Set(url string) error {
	p, err := path()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return err
	}
	return os.WriteFile(p, []byte(strings.TrimSpace(url)+"\n"), 0o600)
}

// Matches reports whether the candidate URL is the pinned endpoint, or
// a path under it (e.g. ``<base>/api/mcp`` matches pinned ``<base>``).
func Matches(pinned, candidate string) bool {
	pinned = strings.TrimRight(pinned, "/")
	candidate = strings.TrimRight(candidate, "/")
	if pinned == "" || candidate == "" {
		return false
	}
	return candidate == pinned || strings.HasPrefix(candidate, pinned+"/")
}
