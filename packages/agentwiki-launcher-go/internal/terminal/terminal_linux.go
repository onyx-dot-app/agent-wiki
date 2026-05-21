// Linux branch of the terminal package — write a bash wrapper and hand
// it to whichever terminal emulator the system has. Tries:
//
//  1. $TERMINAL (user override)
//  2. x-terminal-emulator (Debian/Ubuntu alternatives shim)
//  3. gnome-terminal (GNOME default)
//  4. konsole (KDE default)
//  5. xfce4-terminal, mate-terminal, tilix
//  6. xterm (last-resort, always present on X11)
//
// If none of these are present, we run the wrapper directly without a
// terminal — the user only sees the spawn.log, not the live CLI. Better
// than crashing.

package terminal

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// terminalCmd is one candidate terminal emulator and how to invoke it.
// argv is appended to {wrapperPath}: e.g. ["-e"] → exec.Command(term, "-e", wrapper).
// gnome-terminal needs "--" as the args/exec separator; konsole uses "-e".
type terminalCmd struct {
	bin  string
	args []string
}

// terminalCandidates lists emulators in preference order.
var terminalCandidates = []terminalCmd{
	{"x-terminal-emulator", []string{"-e"}}, // Debian alternatives shim
	{"gnome-terminal", []string{"--"}},
	{"konsole", []string{"-e"}},
	{"xfce4-terminal", []string{"-e"}},
	{"mate-terminal", []string{"-e"}},
	{"tilix", []string{"-e"}},
	{"xterm", []string{"-e"}},
}

// OpenInTerminalApp writes a bash wrapper that:
//   - logs lifecycle to ~/.agentwiki/spawn.log
//   - on exit, curls the close-session beacon then rm -rf's the tmpfiles
//   - cd's to opts.Cwd, exports opts.Env, runs opts.Binary with opts.Argv
//
// Then spawns the first installed terminal emulator pointed at the wrapper.
func OpenInTerminalApp(opts Opts) error {
	dir, err := os.MkdirTemp("", "agw-wrap-")
	if err != nil {
		return err
	}
	wrapper := filepath.Join(dir, "run.sh")

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
read -rp "Press Enter to close..." _
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

	candidates := terminalCandidates
	if userTerm := os.Getenv("TERMINAL"); userTerm != "" {
		// User override — try $TERMINAL with -e first, then fall through.
		candidates = append([]terminalCmd{{userTerm, []string{"-e"}}}, candidates...)
	}

	for _, tc := range candidates {
		path, err := exec.LookPath(tc.bin)
		if err != nil {
			continue
		}
		args := append([]string{}, tc.args...)
		args = append(args, "bash", wrapper)
		cmd := exec.Command(path, args...)
		if err := cmd.Start(); err != nil {
			continue
		}
		go func() { _ = cmd.Wait() }()
		return nil
	}

	// No emulator found — run the wrapper headless so the user at
	// least gets the CLI output streamed to stderr and the cleanup
	// trap fires. Better than silent failure.
	cmd := exec.Command("bash", wrapper)
	cmd.Stderr = os.Stderr
	cmd.Stdout = os.Stdout
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("no terminal emulator found and headless bash exec failed: %w", err)
	}
	go func() { _ = cmd.Wait() }()
	return nil
}
