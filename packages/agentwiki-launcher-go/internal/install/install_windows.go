// Windows branch of the install package — register the agentwiki://
// URL scheme under HKCU\Software\Classes via `reg add`. Selected at
// build time via the _windows filename suffix.

package install

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Install registers the agentwiki:// URL handler in the current user's
// registry hive. Idempotent — `reg add /f` overwrites existing values.
//
// Uses reg.exe (always present on Windows) rather than
// golang.org/x/sys/windows/registry to keep the build dep-free and the
// commands testable via RenderWindowsRegistryCommands.
func Install(launcherPath string) error {
	abs, err := filepath.Abs(launcherPath)
	if err != nil {
		return err
	}
	cmds := RenderWindowsRegistryCommands(abs)
	for _, c := range cmds {
		out, err := exec.Command(c[0], c[1:]...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("reg add failed (%s): %s: %w", c[2], strings.TrimSpace(string(out)), err)
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(home, ".agentwiki"), 0o755); err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "[agentwiki-launcher] installed URL handler -> %s\n", abs)
	// The .exe builds with -H windowsgui so double-click prints nothing
	// to a console window — the user would see no feedback. Pop a
	// MessageBox so they know install succeeded. Best-effort; if mshta
	// isn't available we still return success.
	showInstalledMessage(abs)
	return nil
}

// showInstalledMessage pops a native MessageBox via mshta (ships with
// every Windows since 2000) so the GUI .exe gives the user some
// feedback on install. Failure is swallowed.
func showInstalledMessage(launcherPath string) {
	script := fmt.Sprintf(
		`MsgBox "AgentWikiLauncher installed at " & %s & vbCrLf & vbCrLf & "Click Run Agent in the wiki to continue.", 64, "AgentWikiLauncher"`,
		vbQuoteWin(launcherPath),
	)
	_ = exec.Command("mshta", "vbscript:Execute("+vbQuoteWin(script)+":close)").Run()
}

// vbQuoteWin escapes a string for a VBScript "..." literal — duplicates
// double quotes and collapses newlines. Local helper so install_windows
// doesn't depend on the dialog package.
func vbQuoteWin(s string) string {
	r := strings.NewReplacer(
		`"`, `""`,
		"\r", " ",
		"\n", " ",
	)
	return `"` + r.Replace(s) + `"`
}
