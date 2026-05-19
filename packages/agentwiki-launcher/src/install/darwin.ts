import { execSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

// CFBundleURLTypes block we splice into the osacompile-generated Info.plist
// so macOS routes `agentwiki://` URLs to this .app.
const URL_TYPES_XML = `  <key>LSUIElement</key>
  <true/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key>
      <string>com.agentwiki.launcher.url</string>
      <key>CFBundleURLSchemes</key>
      <array>
        <string>agentwiki</string>
      </array>
    </dict>
  </array>
`;

function resolveAgentwikiLauncherPath(): string {
  // macOS .app stub inherits a minimal PATH (~/usr/bin:/bin) that
  // doesn't include nvm-managed Node bins. Bake the absolute path of
  // the agentwiki-launcher executable into the stub at install time.
  try {
    return execSync("command -v agentwiki-launcher", {
      encoding: "utf-8",
    }).trim();
  } catch {
    // Fallback: assume it's adjacent to the node running this script
    // (works when invoked as `node dist/install/postinstall.js`).
    return join(process.execPath.replace(/\/node$/, ""), "agentwiki-launcher");
  }
}

function makeAppleScript(): string {
  const launcherPath = resolveAgentwikiLauncherPath();
  const nodePath = process.execPath;
  // AppleScript handles the `open location` Apple Event that macOS
  // dispatches when a `agentwiki://` URL is opened. Plain bash stubs
  // can't receive Apple Events.
  return `on open location theURL
\tset logCmd to "echo [$(date)] open location URL=" & quoted form of theURL & " >> $HOME/.agentwiki/stub.log 2>&1"
\tdo shell script logCmd
\ttry
\t\tdo shell script (quoted form of "${nodePath}") & " " & (quoted form of "${launcherPath}") & " dispatch " & quoted form of theURL & " >> $HOME/.agentwiki/stub.log 2>&1"
\ton error errMsg
\t\tdo shell script "echo [$(date)] error: " & quoted form of errMsg & " >> $HOME/.agentwiki/stub.log 2>&1"
\tend try
end open location

-- Also handle Finder double-click — just exit.
on run
\treturn
end run
`;
}

export function installOnDarwin(): void {
  const dest = join(homedir(), "Applications", "AgentWiki.app");
  const contentsDir = join(dest, "Contents");

  // 1. Write AppleScript source.
  const tmp = mkdtempSync(join(tmpdir(), "agw-applet-"));
  const scriptPath = join(tmp, "AgentWikiLauncher.applescript");
  writeFileSync(scriptPath, makeAppleScript());

  // 2. Compile to .app bundle (replaces any existing).
  rmSync(dest, { recursive: true, force: true });
  mkdirSync(join(homedir(), "Applications"), { recursive: true });
  try {
    execSync(`osacompile -o ${shellQuote(dest)} ${shellQuote(scriptPath)}`);
  } catch (e) {
    console.error("[agentwiki-launcher] osacompile failed:", e);
    rmSync(tmp, { recursive: true, force: true });
    throw e;
  }
  rmSync(tmp, { recursive: true, force: true });

  // 3. Patch the generated Info.plist to register the URL scheme.
  const plistPath = join(contentsDir, "Info.plist");
  const plist = readFileSync(plistPath, "utf-8");
  // Insert URL_TYPES_XML right before the closing </dict>\n</plist>.
  const patched = plist.replace(
    /<\/dict>\s*<\/plist>/,
    `${URL_TYPES_XML}</dict>\n</plist>`,
  );
  writeFileSync(plistPath, patched);

  // 4. Force Launch Services to re-read the bundle.
  try {
    execSync(
      `/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f ${shellQuote(
        dest,
      )}`,
    );
  } catch {
    console.warn(
      "[agentwiki-launcher] lsregister failed; open the .app once manually via Finder to register the URL scheme.",
    );
  }

  // 5. Ensure log dir exists.
  mkdirSync(join(homedir(), ".agentwiki"), { recursive: true, mode: 0o700 });

  console.log(`[agentwiki-launcher] installed ${dest}`);
}

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
