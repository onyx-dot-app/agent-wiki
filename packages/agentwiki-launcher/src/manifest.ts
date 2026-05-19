/**
 * Manifest validator — TypeScript mirror of the backend pydantic model.
 *
 * Enforces the DSL safety rules (no ${token}/${first_turn_prompt} in
 * argv, no ${prompt_file_path} in resume) client-side so a compromised
 * backend can't push a malformed manifest past validation.
 */
export interface CliCheck {
  binary: string;
  version_flag?: string;
  min_version?: string;
  install_hint_url?: string;
}

export interface FirstTurnPromptDelivery {
  method: "prompt_file_flag" | "stdin" | "positional_arg" | "none";
  flag?: string;
}

export interface LaunchBlock {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd?: string;
  // Appended only when the launch comes through with no working dir set
  // (helper falls back to $HOME). Literal flags only — no ${var} allowed.
  unscoped_workdir_argv?: string[];
}

export interface SessionIdCapture {
  source: "file_watch" | "stdout_regex" | "none";
  path?: string;
  pattern?: string;
  extract?: string;
}

export interface Manifest {
  manifest_version: 1;
  id: string;
  name: string;
  tagline: string;
  icon_url: string;
  kind: "local_cli" | "in_app" | "web_handoff";
  cli_check?: CliCheck;
  mcp_config_format?: "claude_json" | "codex_toml" | "none";
  first_turn_prompt_delivery?: FirstTurnPromptDelivery;
  launch?: LaunchBlock;
  resume?: LaunchBlock;
  session_id_capture?: SessionIdCapture;
  task_kind?: string;
}

const ALLOWED_VARS = new Set([
  "token",
  "endpoint",
  "session_id",
  "cli_session_id",
  "working_dir",
  "first_turn_prompt",
  "prompt_file_path",
  "mcp_config_path",
  "home",
  "dirhash",
]);

const VAR_RE = /\$\{([a-z_]+)\}/g;

export class ManifestError extends Error {}

function findVars(s: string): Set<string> {
  const found = new Set<string>();
  let m;
  VAR_RE.lastIndex = 0;
  while ((m = VAR_RE.exec(s)) !== null) found.add(m[1]!);
  return found;
}

function checkString(s: string, where: string): void {
  for (const v of findVars(s)) {
    if (!ALLOWED_VARS.has(v)) {
      throw new ManifestError(
        `unknown interpolation var $\{${v}\} in ${where}`,
      );
    }
  }
}

function validateBlock(b: LaunchBlock, blockName: "launch" | "resume"): void {
  b.argv.forEach((a, i) => {
    checkString(a, `${blockName}.argv[${i}]`);
    if (a.includes("${token}")) {
      throw new ManifestError(
        `$\{token\} forbidden in ${blockName}.argv (token must come via env)`,
      );
    }
    if (a.includes("${first_turn_prompt}")) {
      throw new ManifestError(
        `$\{first_turn_prompt\} forbidden in ${blockName}.argv — use $\{prompt_file_path\}`,
      );
    }
    if (blockName === "resume" && a.includes("${prompt_file_path}")) {
      throw new ManifestError(
        "${prompt_file_path} forbidden in resume.argv — first-turn-only",
      );
    }
  });
  (b.unscoped_workdir_argv ?? []).forEach((a, i) => {
    if (VAR_RE.test(a)) {
      VAR_RE.lastIndex = 0;
      throw new ManifestError(
        `${blockName}.unscoped_workdir_argv[${i}] must be a literal flag (no $\{var\})`,
      );
    }
    VAR_RE.lastIndex = 0;
  });
  for (const [k, v] of Object.entries(b.env)) {
    checkString(v, `${blockName}.env.${k}`);
    if (v.includes("${first_turn_prompt}")) {
      throw new ManifestError(
        `$\{first_turn_prompt\} forbidden in ${blockName}.env.${k}`,
      );
    }
    if (blockName === "resume" && v.includes("${prompt_file_path}")) {
      throw new ManifestError(
        `$\{prompt_file_path\} forbidden in resume.env.${k}`,
      );
    }
  }
  if (b.cwd) {
    checkString(b.cwd, `${blockName}.cwd`);
    if (b.cwd.includes("${first_turn_prompt}")) {
      throw new ManifestError(
        `$\{first_turn_prompt\} forbidden in ${blockName}.cwd`,
      );
    }
    if (blockName === "resume" && b.cwd.includes("${prompt_file_path}")) {
      throw new ManifestError(
        `$\{prompt_file_path\} forbidden in resume.cwd`,
      );
    }
  }
}

export function parseManifest(raw: unknown): Manifest {
  if (typeof raw !== "object" || raw === null) {
    throw new ManifestError("manifest must be object");
  }
  const m = raw as Manifest;
  if (m.manifest_version !== 1) {
    throw new ManifestError(
      `unsupported manifest_version ${m.manifest_version}`,
    );
  }
  if (m.kind === "local_cli") {
    if (!m.launch) throw new ManifestError("local_cli requires launch");
    validateBlock(m.launch, "launch");
    if (m.resume) validateBlock(m.resume, "resume");
  }
  return m;
}
