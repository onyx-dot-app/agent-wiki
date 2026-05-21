// Package install handles first-time setup of the agentwiki:// URL handler
// for the host OS. Each supported OS has its own install_<os>.go that
// implements Install(launcherPath); this file holds pure helpers that
// render the OS-specific config payloads so they're testable from any
// platform.
package install

import (
	"fmt"
)

const linuxDesktopFile = `[Desktop Entry]
Name=AgentWikiLauncher
Comment=Agent Wiki helper — handles agentwiki:// URLs
Exec=%s dispatch %%u
Terminal=false
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/agentwiki;
`

// RenderLinuxDesktopFile returns the .desktop file body that registers
// the agentwiki:// URL scheme to the given launcher binary. The %u
// placeholder is freedesktop's "single URL" field code — xdg-open
// substitutes the agentwiki:// URL at dispatch time.
//
// launcherPath and the %u code are left unquoted: freedesktop spec
// §6.5 says field codes inside quoted arguments are unspecified
// behavior. install_linux.go writes the launcher into
// ~/.local/bin/agentwiki-launcher, which won't contain spaces on
// any sane Linux home directory.
func RenderLinuxDesktopFile(launcherPath string) string {
	return fmt.Sprintf(linuxDesktopFile, launcherPath)
}

// RenderWindowsRegistryCommands returns the `reg add` commands that
// register the agentwiki:// URL scheme under HKCU\Software\Classes.
// Pure for testability — install_windows.go executes them.
func RenderWindowsRegistryCommands(launcherPath string) [][]string {
	root := `HKCU\Software\Classes\agentwiki`
	openCmd := fmt.Sprintf(`"%s" dispatch "%%1"`, launcherPath)
	return [][]string{
		{"reg", "add", root, "/ve", "/t", "REG_SZ", "/d", "URL:AgentWiki Launcher", "/f"},
		{"reg", "add", root, "/v", "URL Protocol", "/t", "REG_SZ", "/d", "", "/f"},
		{"reg", "add", root + `\DefaultIcon`, "/ve", "/t", "REG_SZ", "/d", launcherPath + ",0", "/f"},
		{"reg", "add", root + `\shell\open\command`, "/ve", "/t", "REG_SZ", "/d", openCmd, "/f"},
	}
}
