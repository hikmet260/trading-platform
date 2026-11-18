"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { Settings, CreditCard, FileText, LogOut, User, Moon, Sun, ChevronDown } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useTheme } from "@/context/ThemeContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface Profile {
  name: string;
  email: string;
  avatar: string;
  subscription?: string;
  model?: string;
}

export interface MenuItem {
  label: string;
  value?: string;
  href: string;
  icon: React.ReactNode;
  external?: boolean;
}

const Gemini = (props: React.SVGProps<SVGSVGElement>) => (
  <svg
    height="1em"
    style={{
      flex: "none",
      lineHeight: 1,
    }}
    viewBox="0 0 24 24"
    xmlns="http://www.w3.org/2000/svg"
    width="1em"
    {...props}
  >
    <title>{"Gemini"}</title>
    <defs>
      <linearGradient
        id="lobe-icons-gemini-fill"
        x1="0%"
        x2="68.73%"
        y1="100%"
        y2="30.395%"
      >
        <stop offset="0%" stopColor="#1C7DFF" />
        <stop offset="52.021%" stopColor="#1C69FF" />
        <stop offset="100%" stopColor="#F0DCD6" />
      </linearGradient>
    </defs>
    <path
      d="M12 24A14.304 14.304 0 000 12 14.304 14.304 0 0012 0a14.305 14.305 0 0012 12 14.305 14.305 0 00-12 12"
      fill="url(#lobe-icons-gemini-fill)"
      fillRule="nonzero"
    />
  </svg>
);

export const SAMPLE_PROFILE_DATA: Profile = {
  name: "Eugene An",
  email: "eugene@kokonutui.com",
  avatar: "https://cdn.21st.dev/assets/mirror/4a/4a4db5f6231b91dd468f01f4f7348de844f25c56206a1af37e9f9a8ed6d1628e.jpg",
  subscription: "PRO",
  model: "Gemini 2.0 Flash",
};

export interface ProfileDropdownProps extends React.HTMLAttributes<HTMLDivElement> {
  data?: Profile;
  showTopbar?: boolean;
  onSignOut?: () => void;
  customItems?: MenuItem[];
}

export function ProfileDropdown({
  data = SAMPLE_PROFILE_DATA,
  className,
  onSignOut,
  customItems,
  ...props
}: ProfileDropdownProps) {
  const { theme, setTheme } = useTheme();
  const isLight = theme === "light" || theme === "white";
  const isWhite = isLight;

  const [imageError, setImageError] = React.useState(false);
  const [open, setOpen] = React.useState(false);

  // Compute clean initials for fallback
  const initials = React.useMemo(() => {
    if (!data.name) return "OP";
    const parts = data.name.trim().split(" ");
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return data.name.slice(0, 2).toUpperCase();
  }, [data.name]);

  const defaultMenuItems: MenuItem[] = [
    {
      label: "Profile",
      href: "/settings",
      icon: <User className="w-4 h-4" />,
    },
    {
      label: "Model",
      value: data.model,
      href: "/settings",
      icon: <Gemini className="w-4 h-4" />,
    },
    {
      label: "Subscription",
      value: data.subscription,
      href: "/accounts",
      icon: <CreditCard className="w-4 h-4" />,
    },
    {
      label: "Settings",
      href: "/settings",
      icon: <Settings className="w-4 h-4" />,
    },
    {
      label: "Terms & Policies",
      href: "https://kokonutui.com/",
      icon: <FileText className="w-4 h-4" />,
      external: true,
    },
  ];

  const menuItems = customItems || defaultMenuItems;

  return (
    <div className={cn("relative font-sans", className)} {...props}>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <div className="group relative flex items-center">
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="Open profile menu"
              className={cn(
                "flex items-center gap-1.5 p-1 rounded-full border transition-all duration-200 focus:outline-none cursor-pointer",
                isWhite
                  ? "bg-white/80 border-zinc-200/90 hover:border-zinc-300 hover:bg-zinc-50 shadow-xs text-zinc-700"
                  : "bg-[#18181b]/80 border-[#27272a] hover:border-zinc-600 hover:bg-[#202025] text-zinc-300",
                open &&
                  (isWhite
                    ? "border-cyan-400 ring-2 ring-cyan-500/10"
                    : "border-cyan-500/50 ring-2 ring-cyan-400/20")
              )}
            >
              {/* Compact Theme-Adaptive Avatar Container */}
              <div className="relative shrink-0">
                <div
                  className={cn(
                    "w-7 h-7 sm:w-7.5 sm:h-7.5 rounded-full p-[1.5px] transition-all duration-200 shadow-sm",
                    isWhite
                      ? "bg-gradient-to-br from-cyan-500 via-sky-400 to-teal-500 shadow-cyan-500/20"
                      : "bg-gradient-to-br from-cyan-600 via-sky-500 to-teal-400 shadow-cyan-500/30"
                  )}
                >
                  <div
                    className={cn(
                      "w-full h-full rounded-full overflow-hidden flex items-center justify-center font-mono font-bold text-[11px] select-none transition-colors duration-200",
                      isWhite
                        ? "bg-white text-cyan-700"
                        : "bg-[#131316] text-cyan-400"
                    )}
                  >
                    {data.avatar && !imageError ? (
                      <Image
                        src={data.avatar}
                        alt={data.name}
                        width={30}
                        height={30}
                        onError={() => setImageError(true)}
                        className="w-full h-full object-cover rounded-full"
                      />
                    ) : (
                      <span>{initials}</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Arrow Indicator showing it is openable */}
              <ChevronDown
                className={cn(
                  "w-3.5 h-3.5 mr-1 transition-transform duration-200 shrink-0",
                  open && "rotate-180",
                  isWhite
                    ? "text-zinc-500 group-hover:text-zinc-800"
                    : "text-zinc-400 group-hover:text-cyan-400"
                )}
              />
            </button>
          </DropdownMenuTrigger>

          <DropdownMenuContent
            align="end"
            sideOffset={8}
            className={cn(
              "w-64 p-2 backdrop-blur-md rounded-2xl shadow-xl transition-colors duration-200 origin-top-right z-50 font-sans",
              isWhite
                ? "bg-white/95 border border-zinc-200 shadow-zinc-400/20 text-zinc-900"
                : "bg-[#18181b]/95 border border-[#2d2d34] shadow-black/80 text-zinc-100"
            )}
          >
            {/* Header Card with theme-adaptive avatar */}
            <div
              className={cn(
                "flex items-center gap-2.5 p-2.5 rounded-xl mb-1.5 border transition-colors",
                isWhite
                  ? "bg-zinc-50/90 border-zinc-200/80"
                  : "bg-[#131316]/90 border-[#27272e]"
              )}
            >
              <div
                className={cn(
                  "w-8 h-8 rounded-full p-[1.5px] shrink-0",
                  isWhite
                    ? "bg-gradient-to-br from-cyan-500 via-sky-400 to-teal-500"
                    : "bg-gradient-to-br from-cyan-600 via-sky-500 to-teal-400"
                )}
              >
                <div
                  className={cn(
                    "w-full h-full rounded-full overflow-hidden flex items-center justify-center font-mono font-bold text-[11px]",
                    isWhite
                      ? "bg-white text-cyan-600"
                      : "bg-[#131316] text-cyan-400"
                  )}
                >
                  {data.avatar && !imageError ? (
                    <Image
                      src={data.avatar}
                      alt={data.name}
                      width={32}
                      height={32}
                      onError={() => setImageError(true)}
                      className="w-full h-full object-cover rounded-full"
                    />
                  ) : (
                    <span>{initials}</span>
                  )}
                </div>
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-1">
                  <span
                    className={cn(
                      "text-xs font-semibold truncate",
                      isWhite ? "text-zinc-900" : "text-zinc-100"
                    )}
                  >
                    {data.name}
                  </span>
                  {data.subscription && (
                    <span
                      className={cn(
                        "text-[9px] font-mono font-medium px-1.5 py-0.2 rounded border uppercase shrink-0",
                        isWhite
                          ? "bg-cyan-50 text-cyan-700 border-cyan-200"
                          : "bg-cyan-950/60 text-cyan-300 border-cyan-500/30"
                      )}
                    >
                      {data.subscription}
                    </span>
                  )}
                </div>
                <p
                  className={cn(
                    "text-[10px] font-mono truncate mt-0.5",
                    isWhite ? "text-zinc-500" : "text-zinc-400"
                  )}
                >
                  {data.email}
                </p>
              </div>
            </div>

            {/* Menu Items */}
            <div className="space-y-1">
              {menuItems.map((item) => (
                <DropdownMenuItem key={item.label} asChild>
                  <Link
                    href={item.href}
                    target={item.external ? "_blank" : undefined}
                    rel={item.external ? "noopener noreferrer" : undefined}
                    className={cn(
                      "flex items-center p-2 rounded-xl transition-all duration-200 cursor-pointer group border border-transparent",
                      isWhite
                        ? "hover:bg-zinc-100 hover:border-zinc-200/60 text-zinc-800 hover:text-zinc-950"
                        : "hover:bg-[#222228] hover:border-[#2f2f38] text-zinc-200 hover:text-white"
                    )}
                  >
                    <div className="flex items-center gap-2 flex-1">
                      {item.icon}
                      <span className="text-xs sm:text-sm font-medium leading-tight whitespace-nowrap transition-colors font-sans">
                        {item.label}
                      </span>
                    </div>
                    <div className="shrink-0 ml-auto">
                      {item.value && (
                        <span
                          className={cn(
                            "text-[10px] font-medium rounded-md py-0.5 px-2 font-mono border",
                            item.label === "Model"
                              ? isWhite
                                ? "text-cyan-700 bg-cyan-50 border-cyan-200"
                                : "text-cyan-400 bg-cyan-950/30 border-cyan-500/40"
                              : isWhite
                              ? "text-purple-600 bg-purple-50 border-purple-200"
                              : "text-purple-400 bg-purple-500/10 border-purple-500/20"
                          )}
                        >
                          {item.value}
                        </span>
                      )}
                    </div>
                  </Link>
                </DropdownMenuItem>
              ))}
            </div>

            {/* Theme Switcher in Profile Dropdown */}
            <div
              className={cn(
                "p-2 rounded-xl my-2 border transition-colors font-sans",
                isWhite
                  ? "bg-zinc-50 border-zinc-200/80"
                  : "bg-[#131316] border-[#27272e]"
              )}
            >
              <div className="flex items-center justify-between mb-1.5 px-0.5">
                <span
                  className={cn(
                    "text-[10px] font-medium uppercase tracking-wider font-sans",
                    isWhite ? "text-zinc-500" : "text-zinc-400"
                  )}
                >
                  Workspace Theme
                </span>
                <span
                  className={cn(
                    "text-[10px] font-mono",
                    isWhite
                      ? "text-cyan-700 font-semibold"
                      : "text-cyan-400 font-medium"
                  )}
                >
                  {isWhite ? "White" : "Dark"}
                </span>
              </div>

              <div
                className={cn(
                  "grid grid-cols-2 gap-1 p-0.5 rounded-lg border",
                  isWhite
                    ? "bg-zinc-200/50 border-zinc-200"
                    : "bg-[#1a1a1f] border-[#2d2d34]"
                )}
              >
                <button
                  type="button"
                  onClick={() => setTheme("dark")}
                  className={cn(
                    "flex items-center justify-center gap-1.5 py-1 px-2 rounded-md text-xs font-medium transition-all cursor-pointer font-sans",
                    !isLight
                      ? "bg-[#25252d] text-cyan-400 border border-cyan-500/40 shadow-xs"
                      : "text-zinc-600 hover:text-zinc-900"
                  )}
                >
                  <Moon className="w-3.5 h-3.5" />
                  <span>Dark</span>
                </button>

                <button
                  type="button"
                  onClick={() => setTheme("light")}
                  className={cn(
                    "flex items-center justify-center gap-1.5 py-1 px-2 rounded-md text-xs font-medium transition-all cursor-pointer font-sans",
                    isLight
                      ? "bg-white text-zinc-900 border border-zinc-300 shadow-xs"
                      : "text-zinc-400 hover:text-zinc-200"
                  )}
                >
                  <Sun className="w-3.5 h-3.5 text-amber-500" />
                  <span>Light</span>
                </button>
              </div>
            </div>

            <DropdownMenuSeparator
              className={cn(
                "my-2 h-px",
                isWhite ? "bg-zinc-200" : "bg-[#27272e]"
              )}
            />

            <DropdownMenuItem asChild>
              <button
                type="button"
                onClick={onSignOut}
                className={cn(
                  "w-full flex items-center gap-2.5 p-2 duration-200 rounded-xl cursor-pointer border border-transparent transition-all group",
                  isWhite
                    ? "bg-red-50 text-red-600 hover:bg-red-100 hover:border-red-200"
                    : "bg-red-500/10 text-red-400 hover:bg-red-500/20 hover:border-red-500/30"
                )}
              >
                <LogOut className="w-4 h-4 text-red-500 group-hover:text-red-600" />
                <span className="text-xs sm:text-sm font-medium text-red-500 group-hover:text-red-600">
                  Sign Out
                </span>
              </button>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </div>
      </DropdownMenu>
    </div>
  );
}

export default ProfileDropdown;
