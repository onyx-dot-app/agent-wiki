"use client";

import { SvgBubbleText } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useRequireAuth } from "@/lib/auth";

export default function ChatsPage() {
  const { user, loading } = useRequireAuth();

  if (loading || !user) return <LoadingSpinner center />;

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header icon={SvgBubbleText} title="Chats" divider />
      <SettingsLayouts.Body />
    </SettingsLayouts.Root>
  );
}
