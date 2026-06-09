import "./globals.css";

import * as Tooltip from "@radix-ui/react-tooltip";
import { cn } from "@onyx-ai/opal/utils";
import { DM_Mono, Hanken_Grotesk } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { ChatWidget } from "@/components/chat/ChatWidget";
import { ConfirmProvider } from "@/components/common/ConfirmDialog";
import { AuthProvider } from "@/lib/auth";
import { DraftingProvider } from "@/lib/drafting";
import { SWRProvider } from "@/lib/swr";
import {
  THEME_COOKIE_KEY,
  ThemeProvider,
  type ResolvedTheme,
} from "@/lib/theme-provider";

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

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const cookieStore = await cookies();
  const theme: ResolvedTheme =
    cookieStore.get(THEME_COOKIE_KEY)?.value === "dark" ? "dark" : "light";

  return (
    <html
      lang="en"
      className={cn(
        hankenGrotesk.variable,
        dmMono.variable,
        theme === "dark" && "dark",
      )}
      data-theme={theme}
    >
      <head />
      <body
        style={{
          margin: 0,
          boxSizing: "border-box",
          height: "100%",
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
