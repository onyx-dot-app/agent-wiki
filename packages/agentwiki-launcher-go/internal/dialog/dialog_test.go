package dialog

import "testing"

func TestEscapeAppleScript(t *testing.T) {
	cases := []struct{ in, want string }{
		{`http://localhost:8089`, `http://localhost:8089`},
		{`http://"evil"`, `http://\"evil\"`},
		{`http://has\backslash`, `http://has\\backslash`},
		{"http://has\nnewline", `http://has newline`},
		{"http://has\rcr", `http://has cr`},
		{`http://"\` + "\n", `http://\"\\ `},
	}
	for _, c := range cases {
		got := escapeAppleScript(c.in)
		if got != c.want {
			t.Errorf("escapeAppleScript(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestVBQuote(t *testing.T) {
	cases := []struct{ in, want string }{
		{`hello`, `"hello"`},
		{`with "quotes"`, `"with ""quotes"""`},
		{"with\nnewline", `"with newline"`},
		{"with\rcr", `"with cr"`},
		{``, `""`},
	}
	for _, c := range cases {
		got := vbQuote(c.in)
		if got != c.want {
			t.Errorf("vbQuote(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
