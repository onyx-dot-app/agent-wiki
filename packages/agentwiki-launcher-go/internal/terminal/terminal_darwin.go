// macOS branch of the terminal package — write a .command bash wrapper
// and hand it to Terminal.app via LaunchServices. Selected at build
// time via the _darwin filename suffix. No AppleEvents, no TCC prompts.

package terminal

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// OpenInTerminalApp writes a run.command wrapper that:
//   - logs lifecycle to ~/.agentwiki/spawn.log
//   - on exit, curls the close-session beacon then rm -rf's the tmpfiles
//   - cd's to opts.Cwd, exports opts.Env, runs opts.Binary with opts.Argv
//
// Then shells out to ``open -a Terminal.app <wrapper>``.
func OpenInTerminalApp(opts Opts) error {
	dir, err := os.MkdirTemp("", "agw-wrap-")
	if err != nil {
		return err
	}
	wrapper := filepath.Join(dir, "run.command")

	var envExports strings.Builder
	for k, v := range opts.Env {
		fmt.Fprintf(&envExports, "export %s=%s\n", k, shellQuote(v))
	}

	argv := make([]string, 0, len(opts.Argv))
	for _, a := range opts.Argv {
		argv = append(argv, shellQuote(a))
	}
	argvQuoted := strings.Join(argv, " ")

	clean := make([]string, 0, len(opts.TmpfilesToClean)+2)
	for _, p := range opts.TmpfilesToClean {
		clean = append(clean, shellQuote(p))
	}
	clean = append(clean, shellQuote(wrapper), shellQuote(dir))
	cleanList := strings.Join(clean, " ")

	closeLine := ""
	if opts.CloseOnExitURL != "" {
		closeLine = fmt.Sprintf(
			`curl -s -o /dev/null -X POST %s -H %s -H 'Content-Type: application/json' -d '{"reason":"helper_exit"}' || true`,
			shellQuote(opts.CloseOnExitURL),
			shellQuote("Authorization: Bearer "+opts.CloseOnExitToken),
		)
	}

	logQueued(opts)

	script := fmt.Sprintf(`#!/bin/bash
LOG="$HOME/.agentwiki/spawn.log"
mkdir -p "$HOME/.agentwiki"
echo "[$(date)] wrapper start cwd=%s bin=%s" >> "$LOG" 2>&1
__agentwiki_on_exit() {
  local rc=$?
  echo "[$(date)] wrapper exit code=$rc" >> "$LOG"
  %s
  rm -rf %s
}
trap __agentwiki_on_exit EXIT
cd %s 2>>"$LOG" || { echo "cd failed" >> "$LOG"; exit 1; }
%s
echo "[$(date)] PATH=$PATH" >> "$LOG"
echo "[$(date)] which: $(command -v %s 2>&1 || echo NOT_FOUND)" >> "$LOG"
echo "[$(date)] launching %s" >> "$LOG"
%s %s
echo "[$(date)] %s exited code=$?" >> "$LOG"
`,
		opts.Cwd, opts.Binary,
		closeLine,
		cleanList,
		shellQuote(opts.Cwd),
		envExports.String(),
		shellQuote(opts.Binary),
		opts.Binary,
		shellQuote(opts.Binary), argvQuoted,
		opts.Binary,
	)
	if err := os.WriteFile(wrapper, []byte(script), 0o700); err != nil {
		return err
	}

	cmd := exec.Command("open", "-a", "Terminal.app", wrapper)
	// Detach: don't wait for Terminal.app.
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("open Terminal: %w", err)
	}
	go func() { _ = cmd.Wait() }()
	return nil
}
