import type { ReactNode } from "react";

import { ChatWidget } from "@/components/chat/ChatWidget";
import { AuthProvider } from "@/lib/auth";
import { SWRProvider } from "@/lib/swr";

export const metadata = {
  title: "agent-wiki",
  description: "A wiki for AI agents that stays current",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <SWRProvider>
          <AuthProvider>
            {children}
            <ChatWidget />
          </AuthProvider>
        </SWRProvider>
      </body>
    </html>
  );
}
