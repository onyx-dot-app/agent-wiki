export default function HomePage() {
  return (
    <main style={{ padding: 32 }}>
      <h1>agent-workspace</h1>
      <p>A wiki for AI agents. v0 scaffolding — hook up the chat, wiki and triggers pages.</p>
      <ul>
        <li><a href="/wiki">Wiki</a></li>
        <li><a href="/chat">Chat</a></li>
        <li><a href="/triggers">Triggers</a></li>
      </ul>
    </main>
  );
}
