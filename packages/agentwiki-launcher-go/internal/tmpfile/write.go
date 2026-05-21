// Package tmpfile writes short-lived 0600 files under a per-launch tmp dir.
// Used for MCP config files and the first-turn prompt tmpfile.
package tmpfile

import (
	"os"
	"path/filepath"
)

// WriteSecure creates a tmp dir under os.TempDir(), drops a single file
// with the given suffix containing `content`, and returns the file path.
// Caller is responsible for cleanup (the bash wrapper trap does this in
// the spawn flow).
func WriteSecure(content, suffix string) (string, error) {
	dir, err := os.MkdirTemp("", "agw-")
	if err != nil {
		return "", err
	}
	f, err := os.CreateTemp(dir, "agw-*"+suffix)
	if err != nil {
		return "", err
	}
	defer f.Close()
	if err := os.Chmod(f.Name(), 0o600); err != nil {
		return "", err
	}
	if _, err := f.WriteString(content); err != nil {
		return "", err
	}
	return f.Name(), nil
}

// EnsureDir creates ~/.agentwiki and returns the path.
func EnsureAgentwikiDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	d := filepath.Join(home, ".agentwiki")
	if err := os.MkdirAll(d, 0o700); err != nil {
		return "", err
	}
	return d, nil
}
