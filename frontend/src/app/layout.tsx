import type { ReactNode } from "react";

export const metadata = {
  title: "agent-workspace",
  description: "A wiki for AI agents that stays current",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
