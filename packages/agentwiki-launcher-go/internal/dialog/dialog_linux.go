// Linux branch of the dialog package — try zenity then kdialog. Both
// are standard on GNOME/KDE; without either we fall through and accept
// silently (URL handler still works, just no confirmation prompt).

package dialog

import (
	"fmt"
	"os/exec"
)

// ConfirmPin asks the user to pin rawURL as the trusted wiki endpoint.
// Returns true on accept, false on cancel. Falls through to true if
// neither zenity nor kdialog is present — Linux desktops without a
// dialog tool can't show a prompt; accepting silently keeps the URL
// handler usable without forcing every distro to ship zenity.
func ConfirmPin(rawURL string) bool {
	text := fmt.Sprintf("Pin %s as your agent-wiki endpoint?\n\nThis launcher will only accept Run Agent requests from this URL.", rawURL)
	return runLinuxPrompt("AgentWikiLauncher — Pin endpoint", text, "Pin", "Cancel")
}

// ConfirmSwitch asks the user to move the trust pin between URLs.
// Default action is Cancel (destructive default) — falling through on
// missing dialog tools therefore rejects the switch, matching the
// safer-by-default behavior of the macOS dialog.
func ConfirmSwitch(oldURL, newURL string) bool {
	text := fmt.Sprintf(
		"Switch your pinned wiki endpoint?\n\nCurrent: %s\nNew:     %s\n\nOnly do this if you trust the new URL.",
		oldURL, newURL,
	)
	return runLinuxPromptDestructive("AgentWikiLauncher — Switch endpoint", text, "Switch", "Cancel")
}

// runLinuxPrompt shows a yes/no dialog defaulting to accept.
// Returns true on accept OR if no dialog tool is available.
func runLinuxPrompt(title, text, okLabel, cancelLabel string) bool {
	if path, err := exec.LookPath("zenity"); err == nil {
		return exec.Command(path,
			"--question",
			"--title="+title,
			"--text="+text,
			"--ok-label="+okLabel,
			"--cancel-label="+cancelLabel,
		).Run() == nil
	}
	if path, err := exec.LookPath("kdialog"); err == nil {
		return exec.Command(path,
			"--title", title,
			"--yesno", text,
		).Run() == nil
	}
	return true
}

// runLinuxPromptDestructive shows a yes/no dialog defaulting to cancel.
// Returns true ONLY on explicit accept.
func runLinuxPromptDestructive(title, text, okLabel, cancelLabel string) bool {
	if path, err := exec.LookPath("zenity"); err == nil {
		// zenity --question can default to cancel via --default-cancel.
		return exec.Command(path,
			"--question",
			"--default-cancel",
			"--title="+title,
			"--text="+text,
			"--ok-label="+okLabel,
			"--cancel-label="+cancelLabel,
		).Run() == nil
	}
	if path, err := exec.LookPath("kdialog"); err == nil {
		// kdialog --warningyesno highlights cancel as the safe default.
		return exec.Command(path,
			"--title", title,
			"--warningyesno", text,
		).Run() == nil
	}
	return false
}
