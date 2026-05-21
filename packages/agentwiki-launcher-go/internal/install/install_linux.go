// Linux branch of the install package — register the agentwiki:// URL
// scheme by writing a freedesktop .desktop file under
// ~/.local/share/applications and pointing xdg-mime at it. Selected at
// build time via the _linux filename suffix.

package install

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Install writes the .desktop handler for the agentwiki:// scheme and
// registers it via xdg-mime. Idempotent — re-running overwrites the
// .desktop file with the current launcher path.
func Install(launcherPath string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	appsDir := filepath.Join(home, ".local", "share", "applications")
	if err := os.MkdirAll(appsDir, 0o755); err != nil {
		return err
	}
	desktopPath := filepath.Join(appsDir, "agentwiki-launcher.desktop")
	body := RenderLinuxDesktopFile(launcherPath)
	if err := os.WriteFile(desktopPath, []byte(body), 0o644); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(home, ".agentwiki"), 0o700); err != nil {
		return err
	}
	// xdg-mime + update-desktop-database — non-fatal if missing.
	// Distros without freedesktop tooling still get the file written;
	// the user can wire it up manually.
	if out, err := exec.Command("xdg-mime", "default", "agentwiki-launcher.desktop", "x-scheme-handler/agentwiki").CombinedOutput(); err != nil {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = err.Error()
		}
		fmt.Fprintf(os.Stderr, "[agentwiki-launcher] xdg-mime failed (non-fatal): %s\n", msg)
	}
	if out, err := exec.Command("update-desktop-database", appsDir).CombinedOutput(); err != nil {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = err.Error()
		}
		fmt.Fprintf(os.Stderr, "[agentwiki-launcher] update-desktop-database failed (non-fatal): %s\n", msg)
	}
	fmt.Fprintf(os.Stdout, "[agentwiki-launcher] installed %s\n", desktopPath)
	return nil
}
