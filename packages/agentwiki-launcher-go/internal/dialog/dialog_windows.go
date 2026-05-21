// Windows branch of the dialog package — native MessageBox via mshta.
// mshta.exe ships with every Windows install since 2000 and renders
// HTML/JS inline, so a one-liner VBScript MsgBox via JS is enough to
// get a native dialog without bringing in PowerShell.

package dialog

import (
	"fmt"
	"os/exec"
)

// runMessageBox shows a yes/no MessageBox and returns true on Yes.
// vbButtons: 4 = vbYesNo, 36 = vbYesNo+vbQuestion, 35 = vbYesNoCancel+vbCritical.
// defaultBtn: 0 = first button (Yes), 256 = second button (No).
// Return code: 6 = vbYes, 7 = vbNo.
func runMessageBox(title, text string, vbButtons, defaultBtn int) bool {
	script := fmt.Sprintf(
		`Dim r : r = MsgBox(%s, %d, %s) : If r = 6 Then WScript.Quit(0) Else WScript.Quit(1)`,
		vbQuote(text), vbButtons+defaultBtn, vbQuote(title),
	)
	// mshta with vbscript: URI runs MsgBox synchronously; exit code maps
	// to launcher's bool.
	cmd := exec.Command("mshta", "vbscript:Execute("+vbQuote(script)+":close)")
	err := cmd.Run()
	return err == nil
}

// ConfirmPin asks the user to pin rawURL as the trusted wiki endpoint.
// Default button = Yes (matches macOS "Pin" default).
func ConfirmPin(rawURL string) bool {
	body := fmt.Sprintf(
		"Pin %s as your agent-wiki endpoint?\n\nThis launcher will only accept Run Agent requests from this URL.",
		rawURL,
	)
	// vbYesNo (4) + vbQuestion (32) = 36; default first button.
	return runMessageBox("AgentWikiLauncher", body, 36, 0)
}

// ConfirmSwitch asks the user to move the trust pin between URLs.
// Default button = No — keeps a stray agentwiki:// URL from silently
// moving the pin off a trusted host.
func ConfirmSwitch(oldURL, newURL string) bool {
	body := fmt.Sprintf(
		"Switch your pinned wiki endpoint?\n\nCurrent: %s\nNew:     %s\n\nOnly do this if you trust the new URL.",
		oldURL, newURL,
	)
	// vbYesNo (4) + vbExclamation (48) = 52; default second button (256 = vbDefaultButton2).
	return runMessageBox("AgentWikiLauncher", body, 52, 256)
}
