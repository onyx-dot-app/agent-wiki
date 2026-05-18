import type { Manifest } from "./manifest.js";

export interface ExchangePayload {
  session_id: string;
  working_dir: string | null;
  first_turn_prompt: string | null;
  cli_session_id: string | null;
}

export interface ExchangeResponse {
  mcp_token: string;
  endpoint: string;
  manifest: Manifest;
  payload: ExchangePayload;
}

export async function exchange(
  endpoint: string,
  code: string,
  machineId: string,
): Promise<ExchangeResponse> {
  const url = new URL("/api/launch/exchange", endpoint).toString();
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, machine_id: machineId }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`exchange failed ${res.status}: ${body}`);
  }
  return (await res.json()) as ExchangeResponse;
}
