export function renderClaudeJson(opts: { url: string; token: string }): string {
  return JSON.stringify(
    {
      mcpServers: {
        "agent-wiki": {
          type: "http",
          url: opts.url,
          headers: { Authorization: `Bearer ${opts.token}` },
        },
      },
    },
    null,
    2,
  );
}
