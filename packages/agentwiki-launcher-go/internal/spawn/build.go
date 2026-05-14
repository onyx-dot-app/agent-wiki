// Package spawn assembles the final spawn command from a manifest +
// exchange payload + interpolation context.
package spawn

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/allowed"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/interpolate"
	"github.com/onyx-dot-app/agent-wiki/packages/agentwiki-launcher-go/internal/manifest"
)

type Command struct {
	Binary string
	Argv   []string
	Env    map[string]string
	Cwd    string
}

type Opts struct {
	Manifest       *manifest.Manifest
	Token          string
	Endpoint       string
	SessionID      string
	CliSessionID   string
	WorkingDir     *string // nil = unscoped fallback
	McpConfigPath  string
	PromptFilePath string
	PromptText     string
	IsResume       bool
}

// ClaudeDirhash mirrors how claude derives the per-project session dir:
// cwd with slashes replaced by dashes. Verified against a real install.
func ClaudeDirhash(cwd string) string {
	return strings.ReplaceAll(cwd, "/", "-")
}

// EnsureScratchDir mints ~/agent-wiki-runs/<session>/ on demand.
func EnsureScratchDir(sessionID string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	d := filepath.Join(home, "agent-wiki-runs", sessionID)
	if err := os.MkdirAll(d, 0o700); err != nil {
		return "", err
	}
	return d, nil
}

func Build(opts Opts) (*Command, error) {
	if opts.Manifest == nil {
		return nil, fmt.Errorf("nil manifest")
	}
	var block *manifest.LaunchBlock
	if opts.IsResume {
		block = opts.Manifest.Resume
		if block == nil {
			return nil, fmt.Errorf("manifest has no resume block")
		}
	} else {
		block = opts.Manifest.Launch
		if block == nil {
			return nil, fmt.Errorf("manifest has no launch block")
		}
	}
	if err := allowed.Assert(block.Binary); err != nil {
		return nil, err
	}

	unscoped := opts.WorkingDir == nil
	var cwd string
	if unscoped {
		s, err := EnsureScratchDir(opts.SessionID)
		if err != nil {
			return nil, err
		}
		cwd = s
	} else {
		cwd = *opts.WorkingDir
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	ctx := &interpolate.Context{
		Token:          opts.Token,
		Endpoint:       opts.Endpoint,
		SessionID:      opts.SessionID,
		CliSessionID:   opts.CliSessionID,
		WorkingDir:     cwd,
		PromptFilePath: opts.PromptFilePath,
		McpConfigPath:  opts.McpConfigPath,
		Home:           home,
		Dirhash:        ClaudeDirhash(cwd),
	}

	argv, err := interpolate.Argv(block.Argv, ctx)
	if err != nil {
		return nil, err
	}
	if unscoped && len(block.UnscopedWorkdirArgv) > 0 {
		argv = append(argv, block.UnscopedWorkdirArgv...)
	}
	if !opts.IsResume && opts.Manifest.FirstTurnPromptDelivery != nil {
		switch opts.Manifest.FirstTurnPromptDelivery.Method {
		case manifest.MethodPromptFileFlag:
			if opts.PromptFilePath != "" {
				flag := opts.Manifest.FirstTurnPromptDelivery.Flag
				if flag == "" {
					flag = "--prompt-file"
				}
				argv = append(argv, flag, opts.PromptFilePath)
			}
		case manifest.MethodPositionalArg:
			if opts.PromptText != "" {
				argv = append(argv, opts.PromptText)
			}
		}
	}

	env, err := interpolate.Env(block.Env, ctx)
	if err != nil {
		return nil, err
	}

	return &Command{
		Binary: block.Binary,
		Argv:   argv,
		Env:    env,
		Cwd:    cwd,
	}, nil
}
