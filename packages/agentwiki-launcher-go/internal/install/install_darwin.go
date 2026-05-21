// macOS branch of the install package — compile the AppleScript .app that
// owns the agentwiki:// URL scheme, patch its Info.plist, and register
// it with LaunchServices. Selected at build time via the _darwin
// filename suffix.

package install

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const urlTypesXML = `  <key>LSUIElement</key>
  <true/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key>
      <string>com.agentwiki.launcher.url</string>
      <key>CFBundleURLSchemes</key>
      <array>
        <string>agentwiki</string>
      </array>
    </dict>
  </array>
`

func appleScript(launcherPath string) string {
	return fmt.Sprintf(`on open location theURL
	set logCmd to "echo [$(date)] open location URL=" & quoted form of theURL & " >> $HOME/.agentwiki/stub.log 2>&1"
	do shell script logCmd
	try
		do shell script (quoted form of "%s") & " dispatch " & quoted form of theURL & " >> $HOME/.agentwiki/stub.log 2>&1"
	on error errMsg
		do shell script "echo [$(date)] error: " & quoted form of errMsg & " >> $HOME/.agentwiki/stub.log 2>&1"
	end try
end open location

-- Finder double-click — exit cleanly.
on run
	return
end run
`, launcherPath)
}

// Install idempotently installs ~/Applications/AgentWiki.app pointing
// at the given absolute path to this binary.
func Install(launcherPath string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	dest := filepath.Join(home, "Applications", "AgentWiki.app")
	contentsDir := filepath.Join(dest, "Contents")

	tmp, err := os.MkdirTemp("", "agw-applet-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	scriptPath := filepath.Join(tmp, "AgentWikiLauncher.applescript")
	if err := os.WriteFile(scriptPath, []byte(appleScript(launcherPath)), 0o644); err != nil {
		return err
	}

	if err := os.RemoveAll(dest); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}

	cmd := exec.Command("osacompile", "-o", dest, scriptPath)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("osacompile failed: %s: %w", string(out), err)
	}

	plistPath := filepath.Join(contentsDir, "Info.plist")
	plist, err := os.ReadFile(plistPath)
	if err != nil {
		return err
	}
	marker := []byte("</dict>\n</plist>")
	if !bytes.Contains(plist, marker) {
		return fmt.Errorf("Info.plist trailing marker not found at %s — osacompile format changed?", plistPath)
	}
	patched := bytes.Replace(plist, marker, []byte(urlTypesXML+"</dict>\n</plist>"), 1)
	if err := os.WriteFile(plistPath, patched, 0o644); err != nil {
		return err
	}

	const lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
	cmd = exec.Command(lsregister, "-f", dest)
	if out, err := cmd.CombinedOutput(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, "[agentwiki-launcher] lsregister failed:", strings.TrimSpace(string(out)))
		// Not fatal — manual open via Finder also registers.
	}

	if err := os.MkdirAll(filepath.Join(home, ".agentwiki"), 0o700); err != nil {
		return err
	}

	fmt.Fprintf(os.Stdout, "[agentwiki-launcher] installed %s\n", dest)
	return nil
}
