type Parsed =
  | { action: "run"; code: string; tool: string; endpoint: string }
  | { action: "probe"; nonce: string; endpoint: string };

export function parseLaunchUri(raw: string): Parsed {
  const url = new URL(raw);
  if (url.protocol !== "agentwiki:") {
    throw new Error(`unknown scheme ${url.protocol}`);
  }
  const action = url.host || url.pathname.replace(/^\//, "");
  const params = url.searchParams;
  if (action === "run") {
    const code = params.get("code") ?? "";
    const tool = params.get("tool") ?? "";
    const endpoint = params.get("endpoint") ?? "";
    if (!code || !tool || !endpoint) throw new Error("missing run params");
    return { action, code, tool, endpoint };
  }
  if (action === "probe") {
    const nonce = params.get("nonce") ?? "";
    const endpoint = params.get("endpoint") ?? "";
    if (!nonce || !endpoint) throw new Error("missing probe params");
    return { action, nonce, endpoint };
  }
  throw new Error(`unknown action ${action}`);
}
