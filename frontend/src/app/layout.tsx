import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";

export const metadata = {
  title: "agent-workspace",
  description: "A wiki for AI agents that stays current",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
