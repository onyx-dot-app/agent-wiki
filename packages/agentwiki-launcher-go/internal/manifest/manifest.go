// Package manifest is the TS-mirror-in-Go of the backend pydantic
// manifest validator. Enforces the same DSL safety rules:
//   - No ${token} in launch.argv / resume.argv.
//   - No ${first_turn_prompt} anywhere.
//   - No ${prompt_file_path} in resume.*.
//   - No unknown ${var} interpolation tokens.
//   - unscoped_workdir_argv entries are literal flags only.
package manifest

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
)

type Method string

const (
	MethodPromptFileFlag Method = "prompt_file_flag"
	MethodStdin          Method = "stdin"
	MethodPositionalArg  Method = "positional_arg"
	MethodNone           Method = "none"
)

type CliCheck struct {
	Binary         string `json:"binary"`
	VersionFlag    string `json:"version_flag,omitempty"`
	MinVersion     string `json:"min_version,omitempty"`
	InstallHintURL string `json:"install_hint_url,omitempty"`
}

type FirstTurnPromptDelivery struct {
	Method Method `json:"method"`
	Flag   string `json:"flag,omitempty"`
}

type LaunchBlock struct {
	Binary              string            `json:"binary"`
	Argv                []string          `json:"argv"`
	Env                 map[string]string `json:"env"`
	Cwd                 string            `json:"cwd,omitempty"`
	UnscopedWorkdirArgv []string          `json:"unscoped_workdir_argv,omitempty"`
}

type SessionIDCapture struct {
	Source  string `json:"source"`
	Path    string `json:"path,omitempty"`
	Pattern string `json:"pattern,omitempty"`
	Extract string `json:"extract,omitempty"`
}

type Manifest struct {
	ManifestVersion         int                      `json:"manifest_version"`
	ID                      string                   `json:"id"`
	Name                    string                   `json:"name"`
	Tagline                 string                   `json:"tagline"`
	IconURL                 string                   `json:"icon_url"`
	Kind                    string                   `json:"kind"`
	CliCheck                *CliCheck                `json:"cli_check,omitempty"`
	McpConfigFormat         string                   `json:"mcp_config_format,omitempty"`
	FirstTurnPromptDelivery *FirstTurnPromptDelivery `json:"first_turn_prompt_delivery,omitempty"`
	Launch                  *LaunchBlock             `json:"launch,omitempty"`
	Resume                  *LaunchBlock             `json:"resume,omitempty"`
	SessionIDCapture        *SessionIDCapture        `json:"session_id_capture,omitempty"`
	TaskKind                string                   `json:"task_kind,omitempty"`
}

var allowedVars = map[string]struct{}{
	"token": {}, "endpoint": {}, "session_id": {}, "cli_session_id": {},
	"working_dir": {}, "first_turn_prompt": {}, "prompt_file_path": {},
	"mcp_config_path": {}, "home": {}, "dirhash": {},
}

var varRE = regexp.MustCompile(`\$\{([a-z_]+)\}`)

func checkString(s, where string) error {
	matches := varRE.FindAllStringSubmatch(s, -1)
	var unknown []string
	seen := map[string]bool{}
	for _, m := range matches {
		v := m[1]
		if seen[v] {
			continue
		}
		seen[v] = true
		if _, ok := allowedVars[v]; !ok {
			unknown = append(unknown, v)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return fmt.Errorf("unknown interpolation var(s) %v in %s", unknown, where)
	}
	return nil
}

func validateBlock(b *LaunchBlock, name string) error {
	if b == nil {
		return nil
	}
	for i, a := range b.Argv {
		where := fmt.Sprintf("%s.argv[%d]", name, i)
		if err := checkString(a, where); err != nil {
			return err
		}
		if regexp.MustCompile(`\$\{token\}`).MatchString(a) {
			return fmt.Errorf("${token} forbidden in %s (token must come via env)", where)
		}
		if regexp.MustCompile(`\$\{first_turn_prompt\}`).MatchString(a) {
			return fmt.Errorf("${first_turn_prompt} forbidden in %s — reference ${prompt_file_path}", where)
		}
		if name == "resume" && regexp.MustCompile(`\$\{prompt_file_path\}`).MatchString(a) {
			return fmt.Errorf("${prompt_file_path} forbidden in resume.argv — first-turn only")
		}
	}
	for i, a := range b.UnscopedWorkdirArgv {
		if varRE.MatchString(a) {
			return fmt.Errorf("%s.unscoped_workdir_argv[%d] must be literal flag (no ${var})", name, i)
		}
	}
	for k, v := range b.Env {
		where := fmt.Sprintf("%s.env.%s", name, k)
		if err := checkString(v, where); err != nil {
			return err
		}
		if regexp.MustCompile(`\$\{first_turn_prompt\}`).MatchString(v) {
			return fmt.Errorf("${first_turn_prompt} forbidden in %s", where)
		}
		if name == "resume" && regexp.MustCompile(`\$\{prompt_file_path\}`).MatchString(v) {
			return fmt.Errorf("${prompt_file_path} forbidden in %s", where)
		}
	}
	if b.Cwd != "" {
		if err := checkString(b.Cwd, name+".cwd"); err != nil {
			return err
		}
		if regexp.MustCompile(`\$\{first_turn_prompt\}`).MatchString(b.Cwd) {
			return fmt.Errorf("${first_turn_prompt} forbidden in %s.cwd", name)
		}
	}
	return nil
}

func Parse(raw []byte) (*Manifest, error) {
	var m Manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("manifest unmarshal: %w", err)
	}
	if m.ManifestVersion != 1 {
		return nil, fmt.Errorf("unsupported manifest_version %d", m.ManifestVersion)
	}
	if m.Kind == "local_cli" {
		if m.Launch == nil {
			return nil, fmt.Errorf("local_cli requires launch block")
		}
		if err := validateBlock(m.Launch, "launch"); err != nil {
			return nil, err
		}
		if m.Resume != nil {
			if err := validateBlock(m.Resume, "resume"); err != nil {
				return nil, err
			}
		}
	}
	return &m, nil
}
