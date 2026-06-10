"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface AppLayoutState {
  headerContent: ReactNode;
  leftPanelContent: ReactNode | null;
  actionSidebarContent: ReactNode | null;
  isActionSidebarOpen: boolean;
}

interface AppLayoutContextValue extends AppLayoutState {
  setHeaderContent: (node: ReactNode) => void;
  clearHeaderContent: () => void;
  setLeftPanelContent: (node: ReactNode) => void;
  clearLeftPanelContent: () => void;
  openActionSidebar: (content: ReactNode) => void;
  closeActionSidebar: () => void;
}

const AppLayoutContext = createContext<AppLayoutContextValue | null>(null);

export function AppLayoutProvider({ children }: { children: ReactNode }) {
  const [headerContent, setHeaderContent] = useState<ReactNode>(null);
  const [leftPanelContent, setLeftPanelContent] = useState<ReactNode | null>(
    null,
  );
  const [actionSidebarContent, setActionSidebarContent] =
    useState<ReactNode | null>(null);
  const [isActionSidebarOpen, setIsActionSidebarOpen] = useState(false);

  const clearHeaderContent = useCallback(() => setHeaderContent(null), []);
  const clearLeftPanelContent = useCallback(
    () => setLeftPanelContent(null),
    [],
  );
  const openActionSidebar = useCallback((content: ReactNode) => {
    setActionSidebarContent(content);
    setIsActionSidebarOpen(true);
  }, []);
  const closeActionSidebar = useCallback(() => {
    setIsActionSidebarOpen(false);
    setActionSidebarContent(null);
  }, []);

  const value = useMemo<AppLayoutContextValue>(
    () => ({
      headerContent,
      leftPanelContent,
      actionSidebarContent,
      isActionSidebarOpen,
      setHeaderContent,
      clearHeaderContent,
      setLeftPanelContent,
      clearLeftPanelContent,
      openActionSidebar,
      closeActionSidebar,
    }),
    [
      headerContent,
      leftPanelContent,
      actionSidebarContent,
      isActionSidebarOpen,
      clearHeaderContent,
      clearLeftPanelContent,
      openActionSidebar,
      closeActionSidebar,
    ],
  );

  return (
    <AppLayoutContext.Provider value={value}>
      {children}
    </AppLayoutContext.Provider>
  );
}

export function useAppLayout(): AppLayoutContextValue {
  const ctx = useContext(AppLayoutContext);
  if (!ctx)
    throw new Error("useAppLayout must be used inside AppLayoutProvider");
  return ctx;
}
