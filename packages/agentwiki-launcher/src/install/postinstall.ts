import { platform } from "node:process";

async function main() {
  try {
    if (platform === "darwin") {
      const m = await import("./darwin.js");
      m.installOnDarwin();
    } else if (platform === "linux") {
      const m = await import("./linux.js");
      m.installOnLinux();
    } else if (platform === "win32") {
      const m = await import("./win32.js");
      m.installOnWin32();
    }
  } catch (e) {
    console.warn("[agentwiki-launcher] postinstall failed:", e);
    console.warn(
      "Run `agentwiki-launcher set-endpoint <wiki-url>` manually after pointing it at your wiki.",
    );
  }
}

await main();
