// macOS branch of the dialog package — native AppleScript prompts via
// osascript. Selected at build time via the _darwin filename suffix.

package dialog

import (
	"fmt"
	"os/exec"
)

// ConfirmPin asks the user to pin rawURL as the trusted wiki endpoint.
func ConfirmPin(rawURL string) bool {
	script := fmt.Sprintf(
		"display dialog \"Pin %s as your agent-wiki endpoint?\n\nThis launcher will only accept Run Agent requests from this URL.\" buttons {\"Cancel\", \"Pin\"} default button \"Pin\" with title \"AgentWikiLauncher\" with icon note",
		escapeAppleScript(rawURL),
	)
	return exec.Command("osascript", "-e", script).Run() == nil
}

// ConfirmSwitch asks the user to move the trust pin from one URL to
// another. Default Cancel — keeps a stray agentwiki:// URL from silently
// moving the pin off a trusted host.
//
// Only the variable URL fragments go through escapeAppleScript. The
// fixed scaffolding's \n survives so the message renders across lines.
func ConfirmSwitch(oldURL, newURL string) bool {
	body := fmt.Sprintf(
		"Switch your pinned wiki endpoint?\n\nCurrent: %s\nNew:     %s\n\nOnly do this if you trust the new URL.",
		escapeAppleScript(oldURL), escapeAppleScript(newURL),
	)
	script := fmt.Sprintf(
		"display dialog \"%s\" buttons {\"Cancel\", \"Switch\"} default button \"Cancel\" with title \"AgentWikiLauncher\" with icon caution",
		body,
	)
	return exec.Command("osascript", "-e", script).Run() == nil
}
