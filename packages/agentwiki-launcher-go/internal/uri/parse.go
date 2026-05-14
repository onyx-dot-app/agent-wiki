// Package uri parses agentwiki:// URLs into typed actions.
package uri

import (
	"fmt"
	"net/url"
)

type Action int

const (
	ActionRun Action = iota
	ActionProbe
)

type Parsed struct {
	Action   Action
	Code     string // run only
	Tool     string // run only
	Endpoint string // both
	Nonce    string // probe only
}

func Parse(raw string) (*Parsed, error) {
	if raw == "" {
		return nil, fmt.Errorf("empty URI")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid URI: %w", err)
	}
	if u.Scheme != "agentwiki" {
		return nil, fmt.Errorf("unexpected scheme %q (want agentwiki)", u.Scheme)
	}
	q := u.Query()
	endpoint := q.Get("endpoint")
	if endpoint == "" {
		return nil, fmt.Errorf("missing endpoint param")
	}
	switch u.Host {
	case "run":
		code := q.Get("code")
		tool := q.Get("tool")
		if code == "" || tool == "" {
			return nil, fmt.Errorf("run URI missing code or tool")
		}
		return &Parsed{Action: ActionRun, Code: code, Tool: tool, Endpoint: endpoint}, nil
	case "probe":
		nonce := q.Get("nonce")
		if nonce == "" {
			return nil, fmt.Errorf("probe URI missing nonce")
		}
		return &Parsed{Action: ActionProbe, Nonce: nonce, Endpoint: endpoint}, nil
	default:
		return nil, fmt.Errorf("unknown action %q", u.Host)
	}
}
