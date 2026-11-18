"use client";

import React, { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { useTheme } from "@/context/ThemeContext";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const { theme } = useTheme();
  const isLight = theme === "light" || theme === "white";
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !user && pathname !== "/login") {
      router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
    }
  }, [user, isLoading, pathname, router]);

  if (pathname === "/login") {
    return <>{children}</>;
  }

  if (isLoading) {
    return (
      <div
        className={`min-h-screen flex flex-col items-center justify-center font-sans transition-colors ${
          isLight ? "bg-zinc-50 text-zinc-900" : "bg-[#0b0c10] text-zinc-100"
        }`}
      >
        <div className="flex items-center gap-3">
          <div className="w-5 h-5 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
          <span
            className={`text-sm font-sans font-medium tracking-normal ${
              isLight ? "text-zinc-800" : "text-zinc-100"
            }`}
          >
            Authenticating Terminal Session...
          </span>
        </div>
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return <>{children}</>;
}
