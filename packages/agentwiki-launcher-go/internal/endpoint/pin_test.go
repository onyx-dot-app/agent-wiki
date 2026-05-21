package endpoint

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSetGetRoundTrip(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	if err := Set("http://localhost:8089"); err != nil {
		t.Fatal(err)
	}
	got, err := Get()
	if err != nil {
		t.Fatal(err)
	}
	if got != "http://localhost:8089" {
		t.Errorf("got %q", got)
	}
}

func TestSetWritesMode0600(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	if err := Set("http://x"); err != nil {
		t.Fatal(err)
	}
	home, _ := os.UserHomeDir()
	info, err := os.Stat(filepath.Join(home, ".agentwiki", "endpoint.url"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("perm = %o, want 0600", info.Mode().Perm())
	}
}

func TestGetEmptyWhenAbsent(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	got, err := Get()
	if err != nil {
		t.Fatal(err)
	}
	if got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

func TestMatches(t *testing.T) {
	cases := []struct {
		pinned, candidate string
		want              bool
	}{
		{"http://x:8089", "http://x:8089", true},
		{"http://x:8089/", "http://x:8089", true},
		{"http://x:8089", "http://x:8089/api/launch/exchange", true},
		{"http://x:8089", "http://y:8089", false},
		{"http://x:8089", "http://x:8090", false},
		{"", "http://x:8089", false},
		{"http://x:8089", "", false},
		{"http://x:8089", "http://x:8089evil", false},
	}
	for _, c := range cases {
		if got := Matches(c.pinned, c.candidate); got != c.want {
			t.Errorf("Matches(%q, %q) = %v, want %v", c.pinned, c.candidate, got, c.want)
		}
	}
}
