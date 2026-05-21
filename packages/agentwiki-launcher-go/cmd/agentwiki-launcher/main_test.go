package main

import "testing"

func TestAppleScriptEscapeLiteral(t *testing.T) {
	cases := []struct{ in, want string }{
		{`http://localhost:8089`, `http://localhost:8089`},
		{`http://"evil"`, `http://\"evil\"`},
		{`http://has\backslash`, `http://has\\backslash`},
		{"http://has\nnewline", `http://has newline`},
		{"http://has\rcr", `http://has cr`},
		{`http://"\` + "\n", `http://\"\\ `},
	}
	for _, c := range cases {
		got := appleScriptEscapeLiteral(c.in)
		if got != c.want {
			t.Errorf("escape(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
