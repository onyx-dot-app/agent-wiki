"use client";

import { useEffect, useState } from "react";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { invalidateHelperProbe, probeHelper } from "@/lib/launchers";

type Platform = "mac" | "linux" | "windows" | "unknown";

function detectPlatform(): Platform {
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent.toLowerCase();
  if (ua.includes("mac")) return "mac";
  if (ua.includes("win")) return "windows";
  if (ua.includes("linux")) return "linux";
  return "unknown";
}

const PLATFORM_COPY: Record<
  Exclude<Platform, "unknown">,
  { downloadHref: string; downloadLabel: string; instructions: string }
> = {
  mac: {
    downloadHref: "/api/installer/mac",
    downloadLabel: "Download for macOS",
    instructions:
      "Open the downloaded zip, drag AgentWikiLauncher.app to your Applications folder, then click Run Agent.",
  },
  linux: {
    downloadHref: "/api/installer/linux?arch=amd64",
    downloadLabel: "Download for Linux (amd64)",
    instructions:
      "Extract the tarball, run ./install.sh, then click Run Agent. For arm64, append ?arch=arm64 to the download URL.",
  },
  windows: {
    downloadHref: "/api/installer/windows",
    downloadLabel: "Download for Windows",
    instructions:
      'Extract the zip and double-click install.bat. On the SmartScreen prompt click "More info" → "Run anyway" (the launcher is not yet code-signed). Then click Run Agent.',
  },
};

export function InstallHelperPane({
  onReprobe,
}: {
  onReprobe: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [platform, setPlatform] = useState<Platform>("unknown");

  useEffect(() => {
    setPlatform(detectPlatform());
  }, []);

  async function reprobe() {
    setBusy(true);
    try {
      invalidateHelperProbe();
      // Explicit user gesture — force the iframe probe even though
      // we don't yet have an ever-installed flag.
      await probeHelper({ force: true });
      await onReprobe();
    } finally {
      setBusy(false);
    }
  }

  async function manualTest() {
    setManualBusy(true);
    try {
      invalidateHelperProbe();
      const nonce = `n_${Math.random().toString(36).slice(2)}_${Date.now()}`;
      window.location.href = `agentwiki://probe?nonce=${encodeURIComponent(
        nonce,
      )}&endpoint=${encodeURIComponent(window.location.origin)}`;
      await Promise.resolve(onReprobe());
    } finally {
      setManualBusy(false);
    }
  }

  function download(href: string) {
    window.location.href = href;
  }

  const copy = platform === "unknown" ? null : PLATFORM_COPY[platform];

  return (
    <Card padding="md" border="solid" borderColor="warning" rounding="sm">
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={0.75}
        width="full"
      >
        <Text font="secondary-body" color="text-04" as="p">
          Launcher isn&apos;t installed on this machine.
        </Text>
        {copy ? (
          <>
            <Button
              size="md"
              variant="action"
              onClick={() => download(copy.downloadHref)}
            >
              {copy.downloadLabel}
            </Button>
            <Text font="secondary-body" color="text-04" as="p">
              {copy.instructions}
            </Text>
          </>
        ) : (
          <>
            <Section
              flexDirection="column"
              alignItems="start"
              gap={0.5}
              width="full"
            >
              <Button
                size="md"
                variant="action"
                onClick={() => download("/api/installer/mac")}
              >
                Download for macOS
              </Button>
              <Button
                size="md"
                onClick={() => download("/api/installer/linux?arch=amd64")}
              >
                Download for Linux (amd64)
              </Button>
              <Button
                size="md"
                onClick={() => download("/api/installer/windows")}
              >
                Download for Windows
              </Button>
            </Section>
            <Text font="secondary-body" color="text-04" as="p">
              Pick the build for your OS, run the installer it ships with, then
              click Run Agent.
            </Text>
          </>
        )}
        <Section
          flexDirection="row"
          alignItems="center"
          justifyContent="end"
          gap={0.75}
          width="full"
        >
          <Button
            size="md"
            prominence="tertiary"
            onClick={manualTest}
            disabled={manualBusy}
          >
            Test launcher manually
          </Button>
          <Button size="md" variant="action" onClick={reprobe} disabled={busy}>
            {busy ? "Checking..." : "I've installed it"}
          </Button>
        </Section>
      </Section>
    </Card>
  );
}
