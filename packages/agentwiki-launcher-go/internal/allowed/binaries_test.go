package allowed

import "testing"

func TestAssertAllowed(t *testing.T) {
	for _, b := range []string{"claude", "codex"} {
		if err := Assert(b); err != nil {
			t.Errorf("Assert(%q) unexpected error: %v", b, err)
		}
	}
}

func TestAssertRejects(t *testing.T) {
	for _, b := range []string{"", "rm", "bash", "claude-code", "sh -c claude", "../claude"} {
		if err := Assert(b); err == nil {
			t.Errorf("Assert(%q) should have rejected", b)
		}
	}
}
