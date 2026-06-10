"use client";

import { useEffect, type ReactNode } from "react";
import { WikiTree } from "@/components/wiki/WikiTree";
import { useAppLayout } from "@/sections/app/AppLayoutContext";

export default function WikiLayout({ children }: { children: ReactNode }) {
  const { setLeftPanelContent, clearLeftPanelContent } = useAppLayout();

  useEffect(() => {
    setLeftPanelContent(<WikiTree />);
    return () => clearLeftPanelContent();
  }, [setLeftPanelContent, clearLeftPanelContent]);

  return <>{children}</>;
}
