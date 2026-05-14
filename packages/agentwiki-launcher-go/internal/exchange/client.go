// Package exchange POSTs to /api/launch/exchange and unmarshals the
// response. The bearer is the single-use launch_code from the URI.
package exchange

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

type Payload struct {
	SessionID      string  `json:"session_id"`
	WorkingDir     *string `json:"working_dir"`
	FirstTurnPrompt *string `json:"first_turn_prompt"`
	CliSessionID   *string `json:"cli_session_id"`
}

type Response struct {
	McpToken string          `json:"mcp_token"`
	Endpoint string          `json:"endpoint"`
	Manifest json.RawMessage `json:"manifest"`
	Payload  Payload         `json:"payload"`
}

type request struct {
	Code      string `json:"code"`
	MachineID string `json:"machine_id"`
}

func Exchange(pinned, code, machineID string) (*Response, error) {
	u, err := url.JoinPath(pinned, "api", "launch", "exchange")
	if err != nil {
		return nil, fmt.Errorf("build exchange URL: %w", err)
	}
	body, err := json.Marshal(request{Code: code, MachineID: machineID})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("exchange request: %w", err)
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(res.Body)
	if err != nil {
		return nil, err
	}
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("exchange failed %d: %s", res.StatusCode, string(raw))
	}
	var out Response
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("exchange unmarshal: %w", err)
	}
	return &out, nil
}
