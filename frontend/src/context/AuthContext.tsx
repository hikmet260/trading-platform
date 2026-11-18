"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  AuthUser,
  LoginResult,
  getCurrentUser,
  login as apiLogin,
  logout as apiLogout,
  clearSession,
} from "@/lib/api";
import { fireToast } from "@/context/ToastContext";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";

type AuthContextType = {
  user: AuthUser | null;
  token: string | null;
  isLoading: boolean;
  login: (email: string, password: string, tenantId?: string) => Promise<LoginResult>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const refresh = useCallback(async () => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem("trading-platform-token");
    setToken(stored);
    if (!stored) {
      setUser(null);
      setIsLoading(false);
      return;
    }

    if (stored) {
      startTopLoader("Authenticating...");
    }

    try {
      const current = await getCurrentUser();
      if (current) {
        setUser(current);
      } else {
        clearSession();
        setToken(null);
        setUser(null);
      }
    } catch {
      clearSession();
      setToken(null);
      setUser(null);
    } finally {
      setIsLoading(false);
      stopTopLoader();
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Listen for unauthorized events triggered from apiFetch
  useEffect(() => {
    function handleUnauthorized() {
      clearSession();
      setToken(null);
      setUser(null);
      fireToast("warning", "Your session has expired. Please sign in again.", "Session Expired");
      if (pathname !== "/login") {
        router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
      }
    }

    window.addEventListener("trading:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("trading:unauthorized", handleUnauthorized);
  }, [pathname, router]);

  const login = async (
    email: string,
    password: string,
    tenantId = "local"
  ): Promise<LoginResult> => {
    setIsLoading(true);
    startTopLoader("Authenticating...");
    try {
      const result = await apiLogin(email, password, tenantId);
      if (result.success && result.user) {
        setUser(result.user);
        if (typeof window !== "undefined") {
          setToken(window.localStorage.getItem("trading-platform-token"));
        }
      } else if (result.success) {
        // Fetch user profile if not returned directly
        const current = await getCurrentUser();
        setUser(current);
        if (typeof window !== "undefined") {
          setToken(window.localStorage.getItem("trading-platform-token"));
        }
      }
      return result;
    } finally {
      setIsLoading(false);
      stopTopLoader();
    }
  };

  const logout = async () => {
    setIsLoading(true);
    startTopLoader("Signing out...");
    try {
      await apiLogout();
      setUser(null);
      setToken(null);
      fireToast("info", "You have signed out of your session.", "Signed Out");
      router.replace("/login");
    } finally {
      setIsLoading(false);
      setTimeout(() => stopTopLoader(), 350);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        login,
        logout,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
