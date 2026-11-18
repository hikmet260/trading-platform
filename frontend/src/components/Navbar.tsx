"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Equal, X } from "@aliimam/icons";
import { cn } from "@/lib/utils";
import UserMenu from "@/components/UserMenu";
import { useTheme } from "@/context/ThemeContext";

const navLinks = [
  { href: "/", label: "Terminal" },
  { href: "/trades", label: "Trades & Analytics" },
  { href: "/strategies", label: "Strategies" },
  { href: "/backtest", label: "Backtest Lab" },
  { href: "/accounts", label: "Brokers" },
  { href: "/settings", label: "Settings" },
];

export default function Navbar({
  extraControls,
}: {
  extraControls?: React.ReactNode;
}) {
  const pathname = usePathname();
  const { theme } = useTheme();
  const isLight = theme === "light" || theme === "white";

  const [menuState, setMenuState] = useState(false);

  // Close mobile drawer on route change
  useEffect(() => {
    setMenuState(false);
  }, [pathname]);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 w-full transition-colors font-sans select-none",
        isLight
          ? "border-b border-zinc-200/90 bg-white/95 backdrop-blur-md text-zinc-900 shadow-xs"
          : "border-b border-[#26262b] bg-[#12131a]/95 backdrop-blur-md text-zinc-100"
      )}
    >
      <div className="w-full px-4 sm:px-6">
        {/* Main Straight Horizontal Row spanning full width */}
        <div className="flex items-center justify-between h-14 w-full gap-3 sm:gap-6 flex-nowrap">
          {/* Left: Brand Title */}
          <div className="flex items-center shrink-0">
            <Link
              href="/"
              className="flex items-center group cursor-pointer select-none"
              aria-label="Trading Terminal Home"
            >
              <span
                className={cn(
                  "text-xs sm:text-sm font-semibold tracking-wider uppercase font-mono transition-colors whitespace-nowrap",
                  isLight
                    ? "text-zinc-900 group-hover:text-cyan-700"
                    : "text-zinc-100 group-hover:text-cyan-400"
                )}
              >
                Trading Terminal
              </span>
            </Link>
          </div>

          {/* Center: Desktop Navigation Links (strictly aligned horizontally) */}
          <nav className="hidden lg:flex items-center justify-center gap-1.5 text-xs shrink-0 whitespace-nowrap">
            {navLinks.map((link) => {
              const isActive = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "px-3 py-1.5 rounded-lg text-xs font-sans transition-all cursor-pointer whitespace-nowrap",
                    isActive
                      ? isLight
                        ? "text-cyan-700 font-semibold bg-cyan-50 border border-cyan-200/90 shadow-xs"
                        : "text-cyan-400 font-medium bg-cyan-950/40 border border-cyan-500/40 shadow-xs"
                      : isLight
                      ? "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-[#1f2029]"
                  )}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>

          {/* Right: Controls & User Profile & Mobile Toggle */}
          <div className="flex items-center gap-2.5 sm:gap-3 shrink-0">
            {extraControls && (
              <div className="hidden sm:flex items-center gap-2 shrink-0">
                {extraControls}
              </div>
            )}

            <UserMenu />

            {/* Mobile Drawer Toggle (visible below lg) */}
            <button
              type="button"
              onClick={() => setMenuState(!menuState)}
              aria-label={menuState ? "Close Menu" : "Open Menu"}
              className={cn(
                "lg:hidden p-1.5 rounded-lg transition-colors cursor-pointer shrink-0",
                isLight
                  ? "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100"
                  : "text-zinc-400 hover:text-zinc-100 hover:bg-[#1f2029]"
              )}
            >
              {menuState ? (
                <X className="size-5 transition-transform duration-200" />
              ) : (
                <Equal className="size-5 transition-transform duration-200" />
              )}
            </button>
          </div>
        </div>

        {/* Mobile Collapsible Drawer */}
        {menuState && (
          <div
            className={cn(
              "lg:hidden w-full pt-3 pb-3 border-t animate-in fade-in slide-in-from-top-2 duration-200 space-y-2",
              isLight ? "border-zinc-200 bg-white" : "border-[#26262b] bg-[#12131a]"
            )}
          >
            <ul className="space-y-1">
              {navLinks.map((link) => {
                const isActive = pathname === link.href;
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      onClick={() => setMenuState(false)}
                      className={cn(
                        "flex items-center justify-between px-3 py-2 rounded-lg text-xs transition-colors",
                        isActive
                          ? isLight
                            ? "bg-cyan-50 text-cyan-700 font-semibold border border-cyan-200"
                            : "bg-cyan-950/40 text-cyan-400 font-medium border border-cyan-500/40"
                          : isLight
                          ? "text-zinc-700 hover:text-zinc-900 hover:bg-zinc-100"
                          : "text-zinc-300 hover:text-white hover:bg-[#1f2029]"
                      )}
                    >
                      <span>{link.label}</span>
                      {isActive && (
                        <span
                          className={cn(
                            "w-1.5 h-1.5 rounded-full",
                            isLight ? "bg-cyan-600" : "bg-cyan-400"
                          )}
                        />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>

            {extraControls && (
              <div
                className={cn(
                  "pt-2 border-t flex items-center justify-between text-xs",
                  isLight ? "border-zinc-200" : "border-[#26262b]"
                )}
              >
                <span className={isLight ? "text-zinc-500" : "text-zinc-400"}>Controls:</span>
                <div className="flex items-center gap-2">
                  {extraControls}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
