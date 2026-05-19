import { platform } from "node:process";

async function main() {
  try {
    if (platform === "darwin") {
      const m = await import("./darwin.js");
      m.installOnDarwin();
    } else if (platform === "linux") {
      console.log("[agentwiki-launcher] Linux install — Phase 4.");
    } else if (platform === "win32") {
      console.log("[agentwiki-launcher] Windows install — Phase 4.");
    }
  } catch (e) {
    console.warn("[agentwiki-launcher] postinstall failed:", e);
    console.warn(
      "Run `agentwiki-launcher set-endpoint <wiki-url>` manually after pointing it at your wiki.",
    );
  }
}

await main();
