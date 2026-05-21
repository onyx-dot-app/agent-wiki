package uri

import "testing"

func TestParseRun(t *testing.T) {
	p, err := Parse("agentwiki://run?code=lc_abc&tool=claude-code&endpoint=http%3A%2F%2Flocalhost%3A8089")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if p.Action != ActionRun {
		t.Errorf("action = %v, want ActionRun", p.Action)
	}
	if p.Code != "lc_abc" || p.Tool != "claude-code" || p.Endpoint != "http://localhost:8089" {
		t.Errorf("got %+v", p)
	}
}

func TestParseProbe(t *testing.T) {
	p, err := Parse("agentwiki://probe?nonce=n_xyz&endpoint=https%3A%2F%2Fwiki.example.com")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if p.Action != ActionProbe {
		t.Errorf("action = %v, want ActionProbe", p.Action)
	}
	if p.Nonce != "n_xyz" || p.Endpoint != "https://wiki.example.com" {
		t.Errorf("got %+v", p)
	}
}

func TestParseRejects(t *testing.T) {
	cases := map[string]string{
		"empty":                "",
		"wrong scheme":         "https://run?code=x&tool=y&endpoint=z",
		"unknown action":       "agentwiki://hack?endpoint=x",
		"missing endpoint":     "agentwiki://run?code=x&tool=y",
		"run missing code":    "agentwiki://run?tool=y&endpoint=z",
		"run missing tool":    "agentwiki://run?code=x&endpoint=z",
		"probe missing nonce": "agentwiki://probe?endpoint=z",
	}
	for name, raw := range cases {
		if _, err := Parse(raw); err == nil {
			t.Errorf("%s: expected error", name)
		}
	}
}
