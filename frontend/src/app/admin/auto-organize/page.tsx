"use client";

import { AutoOrganize } from "@/components/admin/AutoOrganize";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { useIsMobile } from "@/lib/viewport";

export default function AdminAutoOrganizePage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Auto Organize"
          description="Let the AI keep the wiki's structure tidy — detecting cleanups like empty folders. Run a sweep to scan the whole wiki."
        />
        <AutoOrganize />
      </main>
    </RequireAdmin>
  );
}
