"use client";

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from "react";

interface AppLayoutState {
  headerContent:       ReactNode;
  actionSidebarContent: ReactNode | null;
  isActionSidebarOpen: boolean;
}

interface AppLayoutContextValue extends AppLayoutState {
  setHeaderContent:    (node: ReactNode) => void;
  clearHeaderContent:  () => void;
  openActionSidebar:   (content: ReactNode) => void;
  closeActionSidebar:  () => void;
}

const AppLayoutContext = createContext<AppLayoutContextValue | null>(null);

export function AppLayoutProvider({ children }: { children: ReactNode }) {
  const [headerContent, setHeaderContent] = useState<ReactNode>(null);
  const [actionSidebarContent, setActionSidebarContent] = useState<ReactNode | null>(null);
  const [isActionSidebarOpen, setIsActionSidebarOpen] = useState(false);

  return (
    <AppLayoutContext.Provider
      value={{
        headerContent,
        actionSidebarContent,
        isActionSidebarOpen,
        setHeaderContent,
        clearHeaderContent: () => setHeaderContent(null),
        openActionSidebar:  (content) => { setActionSidebarContent(content); setIsActionSidebarOpen(true); },
        closeActionSidebar: () => setIsActionSidebarOpen(false),
      }}
    >
      {children}
    </AppLayoutContext.Provider>
  );
}

export function useAppLayout(): AppLayoutContextValue {
  const ctx = useContext(AppLayoutContext);
  if (!ctx) throw new Error("useAppLayout must be used inside AppLayoutProvider");
  return ctx;
}
