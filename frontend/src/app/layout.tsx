import * as Tooltip from "@radix-ui/react-tooltip";
import { DM_Mono, Hanken_Grotesk } from "next/font/google";
import type { ReactNode } from "react";

import { ChatWidget } from "@/components/chat/ChatWidget";
import { ConfirmProvider } from "@/components/common/ConfirmDialog";
import { AuthProvider } from "@/lib/auth";
import { DraftingProvider } from "@/lib/drafting";
import { SWRProvider } from "@/lib/swr";
import { ThemeBootstrapScript, ThemeProvider } from "@/lib/theme-provider";

import "./globals.css";

const hankenGrotesk = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-hanken-grotesk",
  display: "swap",
  fallback: [
    "-apple-system",
    "BlinkMacSystemFont",
    "Segoe UI",
    "Roboto",
    "sans-serif",
  ],
});

const dmMono = DM_Mono({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-dm-mono",
  display: "swap",
  fallback: [
    "SF Mono",
    "Monaco",
    "Cascadia Code",
    "Roboto Mono",
    "Consolas",
    "Courier New",
    "monospace",
  ],
});

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
    <html lang="en" className={`${hankenGrotesk.variable} ${dmMono.variable}`}>
      <head>
        {/* Sets data-theme attribute and .dark class on <html> before React
            hydrates so dark-mode users don't see a light-mode flash. */}
        <ThemeBootstrapScript />
      </head>
      <body
        style={{
          margin: 0,
          boxSizing: "border-box",
        }}
      >
        <SWRProvider>
          <AuthProvider>
            <ThemeProvider>
              <DraftingProvider>
                <Tooltip.Provider delayDuration={300}>
                  <ConfirmProvider>
                    {children}
                    <ChatWidget />
                  </ConfirmProvider>
                </Tooltip.Provider>
              </DraftingProvider>
            </ThemeProvider>
          </AuthProvider>
        </SWRProvider>
      </body>
    </html>
  );
}
