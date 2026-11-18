"use client";

import * as React from "react";
import { useState } from "react";
import { LogIn, Lock, Mail, Eye, EyeOff, ShieldCheck } from "lucide-react";

export interface SignIn2Props {
  onSignIn?: (email: string, password: string) => Promise<void> | void;
  onDemoFill?: () => void;
  isLoading?: boolean;
  externalError?: string;
  title?: string;
  subtitle?: string;
}

const SignIn2 = ({
  onSignIn,
  onDemoFill,
  isLoading = false,
  externalError,
  title = "Trading Terminal",
  subtitle = "Sign in to access institutional execution & market stream",
}: SignIn2Props) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [localError, setLocalError] = useState("");

  const validateEmail = (val: string) => {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val);
  };

  const handleSignIn = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!email || !password) {
      setLocalError("Please enter both email and password.");
      return;
    }
    if (!validateEmail(email)) {
      setLocalError("Please enter a valid email address.");
      return;
    }
    setLocalError("");

    if (onSignIn) {
      await onSignIn(email, password);
    } else {
      alert("Sign in successful! (Demo)");
    }
  };

  const handleFillDemo = () => {
    setEmail("admin@trading.local");
    setPassword("AdminPassword123!");
    setLocalError("");
    onDemoFill?.();
  };

  const activeError = externalError || localError;

  return (
    <div className="min-h-screen w-full relative flex items-center justify-center overflow-hidden font-sans select-none">
      {/* High-Resolution Trading Background with Dark Institutional Overlay */}
      <div
        className="absolute inset-0 bg-cover bg-center bg-no-repeat transition-all duration-700 scale-105"
        style={{
          backgroundImage: `url('https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1920&q=80')`,
        }}
      />
      {/* Multi-layered dark obsidian gradient filter */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#0b0c10]/94 via-[#0b0c10]/88 to-[#0b0c10]/98 backdrop-blur-[3px]" />

      {/* Cyber Obsidian Ambient Cyan Glow */}
      <div className="absolute w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-3xl pointer-events-none -top-10 -left-10" />
      <div className="absolute w-[400px] h-[400px] bg-cyan-500/5 rounded-full blur-3xl pointer-events-none -bottom-10 -right-10" />

      {/* Sign-in Card */}
      <div className="w-full max-w-sm bg-[#12131a]/90 backdrop-blur-xl rounded-3xl shadow-2xl shadow-black/80 p-8 flex flex-col items-center border border-[#1e212d] text-zinc-100 relative z-10 mx-4">
        {/* Top Header Badge */}
        <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-[#181a24] border border-[#272a38] mb-5 shadow-lg shadow-cyan-950/20">
          <LogIn className="w-7 h-7 text-cyan-400" />
        </div>

        <h2 className="text-2xl font-bold mb-1.5 text-center text-zinc-100 tracking-tight">
          {title}
        </h2>
        <p className="text-zinc-400 text-xs mb-6 text-center leading-relaxed">
          {subtitle}
        </p>

        {/* Input Form */}
        <form onSubmit={handleSignIn} className="w-full flex flex-col gap-3 mb-2">
          {/* Email Field */}
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500">
              <Mail className="w-4 h-4" />
            </span>
            <input
              placeholder="Email"
              type="email"
              value={email}
              autoComplete="email"
              disabled={isLoading}
              className="w-full pl-10 pr-3 py-2.5 rounded-xl border border-[#222533] focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500/30 bg-[#0d0e14] text-zinc-100 text-xs placeholder:text-zinc-600 transition-colors font-mono"
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          {/* Password Field */}
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500">
              <Lock className="w-4 h-4" />
            </span>
            <input
              placeholder="Password"
              type={showPassword ? "text" : "password"}
              value={password}
              autoComplete="current-password"
              disabled={isLoading}
              className="w-full pl-10 pr-10 py-2.5 rounded-xl border border-[#222533] focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500/30 bg-[#0d0e14] text-zinc-100 text-xs placeholder:text-zinc-600 transition-colors font-mono"
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer"
            >
              {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>

          {/* Error & Quick Demo Fill Bar */}
          <div className="w-full flex items-center justify-between min-h-[20px] px-1">
            {activeError ? (
              <div className="text-[11px] text-rose-400 font-medium">{activeError}</div>
            ) : (
              <span className="text-[10px] text-zinc-600 font-mono">Demo: admin@trading.local</span>
            )}
            <button
              type="button"
              onClick={handleFillDemo}
              className="text-[11px] text-cyan-400 hover:text-cyan-300 hover:underline font-medium transition-colors cursor-pointer ml-auto"
            >
              Auto-fill Demo
            </button>
          </div>

          {/* Sign In CTA Button (Using System Accent Cyan) */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-bold py-2.5 rounded-xl shadow-lg shadow-cyan-950/40 hover:brightness-105 cursor-pointer transition-all mb-3 mt-2 text-xs flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {isLoading ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                <span>Verifying Session...</span>
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4" />
                <span>Sign In to Terminal</span>
              </>
            )}
          </button>
        </form>

        {/* Divider */}
        <div className="flex items-center w-full my-2">
          <div className="flex-grow border-t border-dashed border-[#222533]"></div>
          <span className="mx-2 text-[10px] font-mono text-zinc-500 uppercase tracking-wider">
            Operator Access
          </span>
          <div className="flex-grow border-t border-dashed border-[#222533]"></div>
        </div>

        {/* Quick Connection Provider Buttons */}
        <div className="flex gap-2.5 w-full justify-center mt-2">
          <button
            type="button"
            onClick={handleFillDemo}
            className="flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl border border-[#222533] bg-[#161822] hover:bg-[#1f2230] text-zinc-300 hover:text-zinc-100 transition grow text-xs font-mono cursor-pointer"
            title="Local Operator Partition"
          >
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span>Local</span>
          </button>
          <button
            type="button"
            onClick={handleFillDemo}
            className="flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl border border-[#222533] bg-[#161822] hover:bg-[#1f2230] text-zinc-300 hover:text-zinc-100 transition grow text-xs font-mono cursor-pointer"
            title="Institutional Paper Sandbox"
          >
            <span className="h-2 w-2 rounded-full bg-cyan-400" />
            <span>Paper</span>
          </button>
        </div>
      </div>
    </div>
  );
};

export { SignIn2 };
