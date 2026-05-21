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
	return nil
}
