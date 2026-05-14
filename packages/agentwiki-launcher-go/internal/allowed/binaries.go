// Package allowed enforces the hardcoded binary allow-list. Closes the RCE
// hole: a compromised manifest can't ask the helper to spawn arbitrary
// commands because the binary name is checked here before exec.
package allowed

import "fmt"

var binaries = map[string]struct{}{
	"claude": {},
	"codex":  {},
}

func Assert(binary string) error {
	if _, ok := binaries[binary]; !ok {
		return fmt.Errorf("binary %q not in helper allow-list", binary)
	}
	return nil
}
