// agentwiki-launcher is the macOS helper for agent-wiki's Run-Agent
// flow. It owns the agentwiki:// URL scheme, exchanges launch codes
// with the wiki backend, materialises MCP config files, and spawns the
// chosen CLI (claude / codex) in Terminal.app.
//
// Subcommands:
//
//	set-endpoint <wiki-url>     Pin the wiki backend URL (required once).
//	run <agentwiki://run?...>   Process a launch URI.
//	probe-ack <agentwiki://probe?...>   Ack the helper-detection probe.
//	dispatch <agentwiki://...>  Route by URI action. The .app stub calls this.
//	install                     (Re)install the macOS .app + URL handler.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"strings"
	"time"

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
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
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
		fmt.Fprintln(os.Stderr, "[agentwiki-launcher] error:", err)
		os.Exit(1)
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
	if runtime.GOOS != "darwin" {
		return fmt.Errorf("install: only macOS supported")
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	return install.InstallDarwin(self)
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
	pinned, err := endpoint.Get()
	if err != nil {
		return err
	}
	if pinned == "" {
		pinned = parsed.Endpoint
		if err := endpoint.Set(pinned); err != nil {
			return err
		}
		fmt.Fprintf(os.Stderr,
			"agentwiki-launcher auto-paired to %s. Run `agentwiki-launcher set-endpoint <backend-url>` if backend is on a different host.\n",
			pinned,
		)
	}
	machineID, err := machine.GetOrCreate()
	if err != nil {
		return err
	}
	u, err := url.JoinPath(pinned, "api", "launch", "probe-ack")
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
		return fmt.Errorf("agentwiki-launcher not configured — run `agentwiki-launcher set-endpoint <wiki-url>` first")
	}
	if !endpoint.Matches(pinned, parsed.Endpoint) {
		return fmt.Errorf("URI endpoint %s does not match pinned %s; refusing", parsed.Endpoint, pinned)
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
