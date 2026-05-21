// Package dialog shows native confirmation dialogs to the user. Used to
// gate the Pin / Switch endpoint flow so a stray agentwiki:// URL can't
// silently re-trust a different wiki backend. Each supported OS has its
// own dialog_<os>.go that implements ConfirmPin / ConfirmSwitch via the
// platform's standard prompt mechanism (osascript on macOS, zenity on
// Linux, mshta on Windows).
package dialog

import "strings"

// escapeAppleScript scrubs quotes / backslashes / newlines so an
// attacker-supplied URL can't break out of an AppleScript dialog
// literal. Newlines collapse to spaces because AppleScript treats them
// as statement separators.
func escapeAppleScript(s string) string {
	return strings.NewReplacer(
		`"`, `\"`,
		`\`, `\\`,
		"\r", " ",
		"\n", " ",
	).Replace(s)
}

// vbQuote wraps a string for embedding inside a VBScript "..." literal.
// VBScript escapes a double quote by doubling it; newlines collapse to
// spaces so the script stays on one line for mshta's vbscript: URI.
func vbQuote(s string) string {
	s = strings.NewReplacer(
		"\r", " ",
		"\n", " ",
	).Replace(s)
	return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
}
