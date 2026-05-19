/**
 * Pick the right terminal-opener for the current platform.
 *
 * Returns the function that ``cli.ts`` calls to spawn an external
 * Terminal/gnome-terminal/wt window holding the agent CLI. Honors
 * ``$AGENTWIKI_TERMINAL`` then ``$TERMINAL`` for explicit overrides
 * inside each platform's opener (darwin uses Terminal.app regardless
 * today; linux/win consult $TERMINAL); platform dispatch itself is
 * non-overridable since the install paths are OS-specific.
 */
import { platform } from "node:process";

import type { SpawnCommand } from "../spawn.js";

export interface OpenOpts extends SpawnCommand {
  tmpfilesToClean: string[];
  closeOnExit?: { url: string; token: string };
}

export type OpenInTerminal = (opts: OpenOpts) => void;

export async function selectTerminalOpener(): Promise<OpenInTerminal> {
  if (platform === "darwin") {
    const m = await import("./darwin.js");
    return m.openInTerminalApp;
  }
  if (platform === "linux") {
    const m = await import("./linux.js");
    return m.openInLinuxTerminal;
  }
  if (platform === "win32") {
    const m = await import("./win32.js");
    return m.openInWindowsTerminal;
  }
  throw new Error(`unsupported platform: ${platform}`);
}
