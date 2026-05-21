package install

import (
	"strings"
	"testing"
)

func TestRenderLinuxDesktopFile(t *testing.T) {
	got := RenderLinuxDesktopFile("/home/me/.local/bin/agentwiki-launcher")
	wantSubstrings := []string{
		"[Desktop Entry]",
		"Name=AgentWikiLauncher",
		"Exec=/home/me/.local/bin/agentwiki-launcher dispatch %u",
		"MimeType=x-scheme-handler/agentwiki;",
		"NoDisplay=true",
	}
	for _, s := range wantSubstrings {
		if !strings.Contains(got, s) {
			t.Errorf("desktop file missing %q\n--- file ---\n%s", s, got)
		}
	}
}

func TestRenderWindowsRegistryCommands(t *testing.T) {
	launcherPath := `C:\Users\me\AppData\Local\AgentWikiLauncher\agentwiki-launcher.exe`
	cmds := RenderWindowsRegistryCommands(launcherPath)
	if len(cmds) != 4 {
		t.Fatalf("expected 4 reg commands, got %d", len(cmds))
	}
	// Every command must invoke reg add.
	for i, c := range cmds {
		if c[0] != "reg" || c[1] != "add" {
			t.Errorf("cmd %d not a reg add: %v", i, c)
		}
	}
	// First three set the URL Protocol scaffolding under HKCU\…\agentwiki.
	root := `HKCU\Software\Classes\agentwiki`
	if cmds[0][2] != root {
		t.Errorf("cmd 0 target %q want %q", cmds[0][2], root)
	}
	if cmds[1][2] != root || !contains(cmds[1], "URL Protocol") {
		t.Errorf("cmd 1 must set URL Protocol on %s: %v", root, cmds[1])
	}
	if cmds[2][2] != root+`\DefaultIcon` {
		t.Errorf("cmd 2 target %q want %q", cmds[2][2], root+`\DefaultIcon`)
	}
	// Fourth wires `shell\open\command` to the launcher with %1.
	openCmd := cmds[3]
	if openCmd[2] != root+`\shell\open\command` {
		t.Errorf("cmd 3 target %q want %q", openCmd[2], root+`\shell\open\command`)
	}
	joined := strings.Join(openCmd, " ")
	if !strings.Contains(joined, launcherPath) {
		t.Errorf("cmd 3 missing launcher path %q: %v", launcherPath, openCmd)
	}
	if !strings.Contains(joined, "dispatch") || !strings.Contains(joined, "%1") {
		t.Errorf("cmd 3 missing `dispatch \"%%1\"`: %v", openCmd)
	}
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}
