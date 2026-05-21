// Package terminal spawns the chosen CLI (claude / codex) in a new
// terminal window. Each supported OS has its own terminal_<os>.go that
// writes a wrapper script (bash on mac/linux, cmd batch on windows) and
// hands it to the platform's terminal emulator.
package terminal

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Opts is the shared cross-platform input to OpenInTerminalApp.
type Opts struct {
	Binary           string
	Argv             []string
	Env              map[string]string
	Cwd              string
	TmpfilesToClean  []string
	CloseOnExitURL   string // POST here on wrapper exit
	CloseOnExitToken string // Bearer for the close call
}

// shellQuote wraps a string for safe single-quoted bash interpolation.
func shellQuote(s string) string {
	return `'` + strings.ReplaceAll(s, `'`, `'\''`) + `'`
}

// logQueued appends a one-line "queued" record to ~/.agentwiki/spawn.log
// from the launcher's own process (the wrapper's own start/exit lines
// land there too). Best-effort — silent on any IO error.
func logQueued(opts Opts) {
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	line := fmt.Sprintf(
		"[%s] queued %s cwd=%s argc=%d\n",
		time.Now().Format(time.RFC1123),
		opts.Binary, opts.Cwd, len(opts.Argv),
	)
	logPath := filepath.Join(home, ".agentwiki", "spawn.log")
	_ = os.MkdirAll(filepath.Dir(logPath), 0o700)
	if f, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600); err == nil {
		_, _ = f.WriteString(line)
		_ = f.Close()
	}
}
