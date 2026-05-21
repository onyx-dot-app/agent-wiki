// agentwiki-launcher is the cross-platform helper for agent-wiki's
// Run-Agent flow. It owns the agentwiki:// URL scheme, exchanges launch
// codes with the wiki backend, materialises MCP config files, and spawns
// the chosen CLI (claude / codex) in a terminal. Platform-specific bits
// live behind build-tag-suffixed files in internal/install, dialog, and
// terminal.
//
// Subcommands:
//
//	set-endpoint <wiki-url>     Pin the wiki backend URL (required once).
//	run <agentwiki://run?...>   Process a launch URI.
//	probe-ack <agentwiki://probe?...>   Ack the helper-detection probe.
//	dispatch <agentwiki://...>  Route by URI action. The OS URL handler calls this.
//	install                     (Re)register the agentwiki:// URL handler.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/dialog"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/endpoint"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/exchange"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/install"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/machine"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/manifest"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/mcpconfig"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/spawn"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/terminal"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/tmpfile"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/trust"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/uri"
)

func main() {
	// Tee stderr to ~/.agentwiki/stub.log so errors are recoverable on
	// Windows GUI builds (where the .exe links with -H windowsgui and
	// has no attached console). Best-effort — silent on any IO error.
	teeStderrToLog()

	// Bare invocation (e.g. user double-clicks the downloaded binary in
	// Finder / Explorer) → auto-install the URL handler. No-op if
	// already installed; idempotent.
	if len(os.Args) < 2 {
		if err := doInstall(); err != nil {
			logErr("[agentwiki-launcher] install error:", err)
			os.Exit(1)
		}
		fmt.Fprintln(os.Stdout,
			"Run `agentwiki-launcher set-endpoint <wiki-url>` next, then click Run Agent in the wiki.",
		)
		return
	}
	sub := os.Args[1]
	arg := ""
	if len(os.Args) >= 3 {
		arg = os.Args[2]
	}
	var err error
	switch sub {
	case "set-endpoint":
		err = doSetEndpoint(arg)
	case "run":
		err = doRun(arg)
	case "probe-ack":
		err = doProbeAck(arg)
	case "dispatch":
		err = doDispatch(arg)
	case "install":
		err = doInstall()
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		logErr("[agentwiki-launcher] error:", err)
		os.Exit(1)
	}
}

// teeStderrToLog redirects os.Stderr through an io.MultiWriter that
// also appends to ~/.agentwiki/stub.log. On macOS / Linux the original
// terminal still sees the message; on Windows GUI builds (where the
// console is detached) the log is the only record.
func teeStderrToLog() {
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	logDir := filepath.Join(home, ".agentwiki")
	if err := os.MkdirAll(logDir, 0o700); err != nil {
		return
	}
	f, err := os.OpenFile(filepath.Join(logDir, "stub.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	// Tag every line with a timestamp so log diff is greppable.
	stamp := fmt.Sprintf("[%s] ", time.Now().Format(time.RFC3339))
	_, _ = io.WriteString(f, stamp+"---- launcher start argv="+strings.Join(os.Args, " ")+" ----\n")
	// We don't reassign os.Stderr here because Go's runtime panics also
	// go directly to fd 2. Instead, wrap downstream writes by setting
	// the global stderr to a MultiWriter — but since callers use
	// `fmt.Fprintln(os.Stderr, ...)` they read os.Stderr each call.
	// So replace os.Stderr with a *os.File pointing at the log AND
	// the original fd 2 via dup. Simplest: just write to the log too
	// from the few error sites — done in the main switch below.
	// Leave os.Stderr alone here; instead expose the log file globally.
	logFile = f
}

var logFile *os.File

// logErr writes to the original stderr AND the log file (if open).
func logErr(args ...any) {
	fmt.Fprintln(os.Stderr, args...)
	if logFile != nil {
		fmt.Fprintln(logFile, args...)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: agentwiki-launcher (set-endpoint <wiki-url> | run <uri> | probe-ack <uri> | dispatch <uri> | install)")
}

func doSetEndpoint(arg string) error {
	if arg == "" {
		return fmt.Errorf("set-endpoint requires a URL")
	}
	if err := endpoint.Set(arg); err != nil {
		return err
	}
	fmt.Printf("agentwiki-launcher pinned to %s\n", strings.TrimSpace(arg))
	return nil
}

func doInstall() error {
	// On Linux, when running from an AppImage, $APPIMAGE points at the
	// .AppImage file on disk — a stable path. os.Executable() instead
	// points at the binary inside the FUSE mount which is ephemeral.
	// Prefer $APPIMAGE so the URL handler keeps working after the
	// install process exits.
	if app := os.Getenv("APPIMAGE"); app != "" {
		return install.Install(app)
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	return install.Install(self)
}

func doDispatch(raw string) error {
	if strings.HasPrefix(raw, "agentwiki://probe") {
		return doProbeAck(raw)
	}
	return doRun(raw)
}

func doProbeAck(raw string) error {
	parsed, err := uri.Parse(raw)
	if err != nil {
		return err
	}
	if parsed.Action != uri.ActionProbe {
		return fmt.Errorf("expected probe URI")
	}
	// Probe does NOT pin the endpoint. It posts an ack to whichever
	// host minted the probe URI (typically the FE origin). The wiki
	// backend may live on a different host (FE-proxy vs API-direct);
	// only the first Run Agent URI's user-confirmed Pin dialog
	// establishes the trusted endpoint.
	ackBase := parsed.Endpoint
	if ackBase == "" {
		if pinned, _ := endpoint.Get(); pinned != "" {
			ackBase = pinned
		} else {
			return fmt.Errorf("probe URI has no endpoint and no pinned endpoint")
		}
	}
	machineID, err := machine.GetOrCreate()
	if err != nil {
		return err
	}
	u, err := url.JoinPath(ackBase, "api", "launch", "probe-ack")
	if err != nil {
		return err
	}
	body, _ := json.Marshal(map[string]any{
		"nonce":       parsed.Nonce,
		"helper_port": 0,
		"machine_id":  machineID,
	})
	req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	return nil
}

func doRun(raw string) error {
	parsed, err := uri.Parse(raw)
	if err != nil {
		return err
	}
	if parsed.Action != uri.ActionRun {
		return fmt.Errorf("expected run URI")
	}
	pinned, err := endpoint.Get()
	if err != nil {
		return err
	}
	if pinned == "" {
		if parsed.Endpoint == "" {
			return fmt.Errorf("agentwiki-launcher not configured — run `agentwiki-launcher set-endpoint <wiki-url>` first")
		}
		if !dialog.ConfirmPin(parsed.Endpoint) {
			return fmt.Errorf("user declined to pin endpoint %s", parsed.Endpoint)
		}
		if err := endpoint.Set(parsed.Endpoint); err != nil {
			return err
		}
		pinned = parsed.Endpoint
	}
	if !endpoint.Matches(pinned, parsed.Endpoint) {
		// Pinned-but-mismatched: ask the user before switching. Keeps
		// anti-phishing posture (only an explicit user gesture changes
		// the trusted endpoint) but unblocks legitimate dev → prod
		// switches without requiring `rm ~/.agentwiki/endpoint.url`.
		if !dialog.ConfirmSwitch(pinned, parsed.Endpoint) {
			return fmt.Errorf("URI endpoint %s does not match pinned %s; user declined to switch", parsed.Endpoint, pinned)
		}
		if err := endpoint.Set(parsed.Endpoint); err != nil {
			return err
		}
		pinned = parsed.Endpoint
	}

	machineID, err := machine.GetOrCreate()
	if err != nil {
		return err
	}

	resp, err := exchange.Exchange(pinned, parsed.Code, machineID)
	if err != nil {
		return err
	}

	man, err := manifest.Parse(resp.Manifest)
	if err != nil {
		return err
	}
	if man.Kind != "local_cli" {
		return fmt.Errorf("unsupported manifest kind %q", man.Kind)
	}

	isResume := resp.Payload.CliSessionID != nil && *resp.Payload.CliSessionID != ""

	mcpURL := resp.Endpoint // <base>/api/mcp
	var mcpConfigPath string
	switch man.McpConfigFormat {
	case "claude_json":
		content, err := mcpconfig.RenderClaudeJSON(mcpURL, resp.McpToken)
		if err != nil {
			return err
		}
		mcpConfigPath, err = tmpfile.WriteSecure(content, ".json")
		if err != nil {
			return err
		}
	case "codex_toml":
		// codex reads MCP servers only from ~/.codex/config.toml — inject
		// a managed block instead of materialising a tmpfile.
		if err := trust.WriteCodexAgentwikiMcp(mcpURL); err != nil {
			return err
		}
	}

	var promptFilePath, promptText string
	if !isResume && resp.Payload.FirstTurnPrompt != nil && man.FirstTurnPromptDelivery != nil {
		switch man.FirstTurnPromptDelivery.Method {
		case manifest.MethodPromptFileFlag:
			p, err := tmpfile.WriteSecure(*resp.Payload.FirstTurnPrompt, ".txt")
			if err != nil {
				return err
			}
			promptFilePath = p
		case manifest.MethodPositionalArg:
			promptText = *resp.Payload.FirstTurnPrompt
		}
	}

	var wd *string
	if resp.Payload.WorkingDir != nil && *resp.Payload.WorkingDir != "" {
		wd = resp.Payload.WorkingDir
	}
	var cliSessionID string
	if resp.Payload.CliSessionID != nil {
		cliSessionID = *resp.Payload.CliSessionID
	}

	cmd, err := spawn.Build(spawn.Opts{
		Manifest:       man,
		Token:          resp.McpToken,
		Endpoint:       pinned,
		SessionID:      resp.Payload.SessionID,
		CliSessionID:   cliSessionID,
		WorkingDir:     wd,
		McpConfigPath:  mcpConfigPath,
		PromptFilePath: promptFilePath,
		PromptText:     promptText,
		IsResume:       isResume,
	})
	if err != nil {
		return err
	}

	// Trust pre-mark only on unscoped fallback launches that opted in.
	if wd == nil && len(man.Launch.UnscopedWorkdirArgv) > 0 {
		switch man.ID {
		case "claude-code":
			_ = trust.MarkClaudeWorkspaceTrusted(cmd.Cwd)
		case "codex":
			_ = trust.MarkCodexProjectTrusted(cmd.Cwd)
		}
	}

	var tmpfiles []string
	if mcpConfigPath != "" {
		tmpfiles = append(tmpfiles, mcpConfigPath)
	}
	if promptFilePath != "" {
		tmpfiles = append(tmpfiles, promptFilePath)
	}

	closeURL, _ := url.JoinPath(pinned, "api", "agent-sessions", resp.Payload.SessionID, "close")

	if err := terminal.OpenInTerminalApp(terminal.Opts{
		Binary:           cmd.Binary,
		Argv:             cmd.Argv,
		Env:              cmd.Env,
		Cwd:              cmd.Cwd,
		TmpfilesToClean:  tmpfiles,
		CloseOnExitURL:   closeURL,
		CloseOnExitToken: resp.McpToken,
	}); err != nil {
		return err
	}

	// Best-effort spawn-ok beacon.
	_ = postSpawnOk(pinned, resp.Payload.SessionID, resp.McpToken)
	return nil
}

func postSpawnOk(pinned, sessionID, token string) error {
	u, err := url.JoinPath(pinned, "api", "agent-sessions", sessionID, "spawn-ok")
	if err != nil {
		return err
	}
	req, err := http.NewRequest(http.MethodPost, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	client := &http.Client{Timeout: 5 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	return nil
}
