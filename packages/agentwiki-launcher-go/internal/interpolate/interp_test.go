package interpolate

import (
	"reflect"
	"testing"
)

func ctx() *Context {
	return &Context{
		Token:         "tok_abc",
		Endpoint:      "http://x:8089",
		SessionID:     "as_1",
		WorkingDir:    "/tmp/work",
		McpConfigPath: "/tmp/mcp.json",
	}
}

func TestStringSubstitutes(t *testing.T) {
	out, err := String("${endpoint}/api/mcp", ctx())
	if err != nil {
		t.Fatal(err)
	}
	if out != "http://x:8089/api/mcp" {
		t.Errorf("got %q", out)
	}
}

func TestStringErrorsOnMissing(t *testing.T) {
	if _, err := String("${cli_session_id}", ctx()); err == nil {
		t.Error("expected error for unset var")
	}
	if _, err := String("${nope}", ctx()); err == nil {
		t.Error("expected error for unknown var")
	}
}

func TestArgv(t *testing.T) {
	got, err := Argv([]string{"--config", "${mcp_config_path}", "--token", "${token}"}, ctx())
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"--config", "/tmp/mcp.json", "--token", "tok_abc"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestArgvSurfacesIndex(t *testing.T) {
	_, err := Argv([]string{"ok", "${nope}"}, ctx())
	if err == nil {
		t.Fatal("expected error")
	}
	if msg := err.Error(); msg[:len("argv[1]:")] != "argv[1]:" {
		t.Errorf("error %q lacks argv[1] prefix", msg)
	}
}

func TestEnv(t *testing.T) {
	got, err := Env(map[string]string{"A": "${session_id}", "B": "static"}, ctx())
	if err != nil {
		t.Fatal(err)
	}
	if got["A"] != "as_1" || got["B"] != "static" {
		t.Errorf("got %v", got)
	}
}
