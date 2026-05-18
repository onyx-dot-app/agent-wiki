export interface InterpolateContext {
  token: string;
  endpoint: string;
  session_id: string;
  cli_session_id: string | null;
  working_dir: string | null;
  prompt_file_path: string | null;
  mcp_config_path: string | null;
  home: string;
  dirhash: string;
}

const RE = /\$\{([a-z_]+)\}/g;

export function interpolate(template: string, ctx: InterpolateContext): string {
  return template.replace(RE, (_, name: string) => {
    const value = (ctx as unknown as Record<string, string | null>)[name];
    if (value === undefined) throw new Error(`unknown var $\{${name}\}`);
    if (value === null) {
      throw new Error(
        `var $\{${name}\} unset in this context (first-turn vs resume mismatch?)`,
      );
    }
    return value;
  });
}

export function interpolateArgv(
  argv: string[],
  ctx: InterpolateContext,
): string[] {
  return argv.map((a) => interpolate(a, ctx));
}

export function interpolateEnv(
  env: Record<string, string>,
  ctx: InterpolateContext,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) out[k] = interpolate(v, ctx);
  return out;
}
