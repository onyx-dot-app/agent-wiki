// Package mcpconfig renders the per-tool MCP config blobs.
package mcpconfig

import "encoding/json"

type claudeJSON struct {
	McpServers map[string]claudeServer `json:"mcpServers"`
}

type claudeServer struct {
	Type    string            `json:"type"`
	URL     string            `json:"url"`
	Headers map[string]string `json:"headers"`
}

func RenderClaudeJSON(url, bearer string) (string, error) {
	cfg := claudeJSON{
		McpServers: map[string]claudeServer{
			"agent-wiki": {
				Type: "http",
				URL:  url,
				Headers: map[string]string{
					"Authorization": "Bearer " + bearer,
				},
			},
		},
	}
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return "", err
	}
	return string(b), nil
}
