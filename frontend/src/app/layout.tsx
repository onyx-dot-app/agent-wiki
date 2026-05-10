import type { ReactNode } from "react";

import { ChatWidget } from "@/components/chat/ChatWidget";
import { AuthProvider } from "@/lib/auth";
import { SWRProvider } from "@/lib/swr";
import { ThemeBootstrapScript, ThemeProvider } from "@/lib/theme-provider";

import "./globals.css";

export const metadata = {
  title: "agent-wiki",
  description: "A wiki for AI agents that stays current",
};

// Render at native scale on mobile rather than the legacy desktop-zoom
// fallback. Required for the responsive layout to behave correctly.
export const viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Sets data-theme on <html> before React hydrates so dark-mode
            users don't see a light-mode flash on first paint. */}
        <ThemeBootstrapScript />
      </head>
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <SWRProvider>
          <AuthProvider>
            <ThemeProvider>
              {children}
              <ChatWidget />
            </ThemeProvider>
          </AuthProvider>
        </SWRProvider>
      </body>
    </html>
  );
}
