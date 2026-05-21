// Windows branch of the terminal package — write a .bat wrapper and
// spawn it in a new cmd window via `cmd /c start ... cmd /k <wrapper>`.
// `cmd /k` keeps the window alive after the CLI exits so the user can
// see output before closing.

package terminal

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// batQuote wraps a value for safe use inside a `set "KEY=VAL"` line.
// cmd.exe inside double quotes treats most chars literal; ^ escapes
// `&` `|` `<` `>` `^` only outside quotes. The %  must be doubled to
// survive batch expansion.
func batQuote(s string) string {
	s = strings.ReplaceAll(s, "%", "%%")
	s = strings.ReplaceAll(s, `"`, `""`)
	return s
}

// argQuote wraps a single CLI arg in double quotes, escaping embedded
// quotes per Windows command-line conventions (a quote inside a quoted
// arg is `\"`).
func argQuote(s string) string {
	if !strings.ContainsAny(s, ` 	"`) {
		return s
	}
	return `"` + strings.ReplaceAll(s, `"`, `\"`) + `"`
}

// OpenInTerminalApp writes a run.bat wrapper that mirrors the mac/linux
// flow:
//   - logs lifecycle to %USERPROFILE%\.agentwiki\spawn.log
//   - on exit, curls the close-session beacon then deletes tmpfiles
//   - cd's to opts.Cwd, sets opts.Env, runs opts.Binary with opts.Argv
//
// Spawned via `cmd /c start "title" cmd /k wrapper.bat` so a new window
// opens and stays open until the user closes it.
func OpenInTerminalApp(opts Opts) error {
	dir, err := os.MkdirTemp("", "agw-wrap-")
	if err != nil {
		return err
	}
	wrapper := filepath.Join(dir, "run.bat")

	var envSet strings.Builder
	for k, v := range opts.Env {
		fmt.Fprintf(&envSet, "set \"%s=%s\"\r\n", k, batQuote(v))
	}

	argv := make([]string, 0, len(opts.Argv))
	for _, a := range opts.Argv {
		argv = append(argv, argQuote(a))
	}
	argvQuoted := strings.Join(argv, " ")

	clean := strings.Builder{}
	for _, p := range opts.TmpfilesToClean {
		fmt.Fprintf(&clean, "del /f /q \"%s\" >NUL 2>&1\r\n", p)
	}
	// Self-cleanup: schedule wrapper + its dir after we exit.
	fmt.Fprintf(&clean, "start /b \"\" cmd /c (timeout /t 1 /nobreak >NUL & del /f /q \"%s\" & rmdir /s /q \"%s\")\r\n", wrapper, dir)

	closeLine := ""
	if opts.CloseOnExitURL != "" {
		// Windows 10+ ships curl.exe. -k/-K aren't needed; the beacon is
		// best-effort and we eat the exit code with `|| ver >NUL`.
		closeLine = fmt.Sprintf(
			`curl.exe -s -o NUL -X POST "%s" -H "Authorization: Bearer %s" -H "Content-Type: application/json" -d "{\"reason\":\"helper_exit\"}" || ver >NUL`+"\r\n",
			opts.CloseOnExitURL, opts.CloseOnExitToken,
		)
	}

	logQueued(opts)

	// Batch script. CRLF line endings — cmd.exe is picky about LF-only.
	script := "@echo off\r\n" +
		fmt.Sprintf("set LOG=%%USERPROFILE%%\\.agentwiki\\spawn.log\r\n") +
		"if not exist \"%USERPROFILE%\\.agentwiki\" mkdir \"%USERPROFILE%\\.agentwiki\"\r\n" +
		fmt.Sprintf("echo [%%DATE%% %%TIME%%] wrapper start cwd=%s bin=%s >> \"%%LOG%%\"\r\n", opts.Cwd, opts.Binary) +
		fmt.Sprintf("cd /d \"%s\" || (echo cd failed >> \"%%LOG%%\" & exit /b 1)\r\n", opts.Cwd) +
		envSet.String() +
		fmt.Sprintf("echo [%%DATE%% %%TIME%%] launching %s >> \"%%LOG%%\"\r\n", opts.Binary) +
		fmt.Sprintf("\"%s\" %s\r\n", opts.Binary, argvQuoted) +
		fmt.Sprintf("set EXITCODE=%%ERRORLEVEL%%\r\n") +
		fmt.Sprintf("echo [%%DATE%% %%TIME%%] %s exited code=%%EXITCODE%% >> \"%%LOG%%\"\r\n", opts.Binary) +
		closeLine +
		clean.String() +
		"exit /b %EXITCODE%\r\n"

	if err := os.WriteFile(wrapper, []byte(script), 0o600); err != nil {
		return err
	}

	// `start "title" cmd /k wrapper.bat` opens a new console window that
	// stays open after the script exits. The launcher process detaches
	// immediately.
	cmd := exec.Command("cmd", "/c", "start", "AgentWikiLauncher", "cmd", "/k", wrapper)
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start cmd window: %w", err)
	}
	go func() { _ = cmd.Wait() }()
	return nil
}
