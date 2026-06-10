"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface ActivitiesContextValue {
  isOpen: boolean;
  toggle: () => void;
}

const ActivitiesContext = createContext<ActivitiesContextValue | null>(null);

export function useActivities(): ActivitiesContextValue {
  const ctx = useContext(ActivitiesContext);
  if (!ctx)
    throw new Error("useActivities must be used within ActivitiesProvider");
  return ctx;
}

interface ActivitiesProviderProps {
  children: ReactNode;
}

export function ActivitiesProvider({ children }: ActivitiesProviderProps) {
  const [isOpen, setIsOpen] = useState(false);
  const value = useMemo<ActivitiesContextValue>(
    () => ({ isOpen, toggle: () => setIsOpen((v) => !v) }),
    [isOpen],
  );
  return (
    <ActivitiesContext.Provider value={value}>
      {children}
    </ActivitiesContext.Provider>
  );
}
