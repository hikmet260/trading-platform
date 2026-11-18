"use client";
import Image from "next/image";

import React, { useState } from "react";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";

export interface LoginPageProps {
  onSignIn?: (email: string, password: string, rememberMe: boolean) => Promise<void> | void;
  isLoading?: boolean;
  externalError?: string;
  onNavigateSignUp?: () => void;
  onForgotPassword?: () => void;
}

export function LoginPage({
  onSignIn,
  isLoading = false,
  externalError,
  onNavigateSignUp,
  onForgotPassword,
}: LoginPageProps) {
  const router = useRouter();
  const [showPassword, setShowPassword] = useState(false);
  const [localError, setLocalError] = useState("");
  const [formData, setFormData] = useState({
    email: "",
    password: "",
    rememberMe: false,
  });

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
  };

  const handleGoSignUp = () => {
    if (onNavigateSignUp) {
      onNavigateSignUp();
    } else {
      router.push("/signup");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.email || !formData.password) {
      setLocalError("Please enter both email and password.");
      return;
    }
    setLocalError("");

    if (onSignIn) {
      await onSignIn(formData.email, formData.password, formData.rememberMe);
    } else {
      console.log("Login submitted:", formData);
    }
  };

  const activeError = externalError || localError;

  return (
    <div className="min-h-screen w-full bg-[#0b0c10] flex flex-col lg:flex-row overflow-hidden font-sans text-white select-none">
      {/* Left Panel - Image Section (Trading Terminal Visuals) */}
      <div className="hidden lg:flex flex-1 relative overflow-hidden bg-[#0d0e14]">
        {/* High-Resolution Unsplash Trading Asset */}
        <div className="absolute inset-0">
          <Image
            fill
            src="https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1920&q=80"
            alt="Institutional Trading Terminal"
            className="w-full h-full object-cover opacity-80"
          />
          {/* Obsidian Vignette and Gradient Overlay */}
          <div className="absolute inset-0 bg-gradient-to-t from-[#0b0c10] via-[#0b0c10]/40 to-transparent" />
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-[#0b0c10]/20 to-[#12131a]" />
        </div>

        {/* Ambient Cyan Accent Glow */}
        <div className="absolute w-[450px] h-[450px] bg-cyan-500/10 rounded-full blur-3xl pointer-events-none -top-10 -left-10" />

        {/* Institutional Trading Banner Card */}
        <div className="absolute bottom-8 left-8 right-8 z-10 p-6 rounded-2xl bg-[#0b0c10]/90 backdrop-blur-md border border-[#1e212d] shadow-2xl">
          <h3 className="text-2xl font-bold text-white font-sans tracking-tight">Trading Terminal</h3>
          <p className="text-xs text-white/90 font-sans mt-2 leading-relaxed">
            High-performance quantitative workspace with real-time WebSocket feeds, deterministic
            risk limits, and automated strategy compilation.
          </p>
        </div>
      </div>

      {/* Right Panel - Form Section */}
      <div className="flex-1 flex items-center justify-center bg-[#12131a] p-6 sm:p-10 z-10">
        <div className="w-full max-w-md">
          {/* Header */}
          <div className="mb-8">
            <span className="text-xs font-semibold text-cyan-400 font-sans tracking-wide uppercase">
              Operator Portal
            </span>
            <h1 className="text-3xl font-bold text-white font-sans mt-2 mb-2 tracking-tight">
              Welcome Back
            </h1>
            <p className="text-sm text-white font-sans">
              Don&apos;t have an account?{" "}
              <button
                type="button"
                onClick={handleGoSignUp}
                className="text-cyan-400 hover:text-cyan-300 font-semibold font-sans transition-colors cursor-pointer"
              >
                Sign up
              </button>
            </p>
          </div>

          {/* Error Message */}
          {activeError && (
            <div className="mb-4 rounded-xl border border-rose-500/50 bg-rose-950/40 p-3 text-xs text-rose-200 font-sans">
              {activeError}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div>
              <label htmlFor="login-email" className="block text-xs font-semibold text-white font-sans mb-2">
                Email Address
              </label>
              <input
                id="login-email"
                type="email"
                name="email"
                value={formData.email}
                onChange={handleInputChange}
                placeholder="operator@trading.local"
                disabled={isLoading}
                className="w-full px-4 py-3 border border-[#272a38] rounded-xl focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30 bg-[#0d0e14] text-white text-sm outline-none transition-colors font-sans placeholder:text-zinc-400"
                required
              />
            </div>

            {/* Password */}
            <div>
              <label htmlFor="login-password" className="block text-xs font-semibold text-white font-sans mb-2">
                Password
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  name="password"
                  value={formData.password}
                  onChange={handleInputChange}
                  placeholder="••••••••••••"
                  disabled={isLoading}
                  className="w-full px-4 py-3 pr-12 border border-[#272a38] rounded-xl focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30 bg-[#0d0e14] text-white text-sm outline-none transition-colors font-sans placeholder:text-zinc-400"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 hover:bg-zinc-800 rounded-lg text-zinc-300 hover:text-white transition-colors cursor-pointer"
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4 text-zinc-300" />
                  ) : (
                    <Eye className="w-4 h-4 text-zinc-300" />
                  )}
                </button>
              </div>
            </div>

            {/* Remember Me + Forgot Password */}
            <div className="flex items-center justify-between text-xs font-sans">
              <label className="flex items-center space-x-2 text-white cursor-pointer">
                <input
                  type="checkbox"
                  name="rememberMe"
                  checked={formData.rememberMe}
                  onChange={handleInputChange}
                  className="w-4 h-4 rounded border-[#272a38] bg-[#0d0e14] text-cyan-500 focus:ring-cyan-500/30 cursor-pointer"
                />
                <span className="text-white font-medium font-sans">Remember me</span>
              </label>
              <button
                type="button"
                onClick={onForgotPassword}
                className="text-xs text-cyan-400 hover:text-cyan-300 font-semibold font-sans transition-colors cursor-pointer"
              >
                Forgot password?
              </button>
            </div>

            {/* Submit Button (Using System Accent Cyan) */}
            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-cyan-500 hover:bg-cyan-400 text-zinc-950 py-3 px-4 rounded-xl font-bold transition-all shadow-lg shadow-cyan-950/50 hover:brightness-105 cursor-pointer disabled:opacity-50 flex items-center justify-center gap-2 text-sm font-sans"
            >
              {isLoading ? (
                <>
                  <div className="w-4 h-4 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                  <span>Verifying Session...</span>
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4" />
                  <span>Sign In</span>
                </>
              )}
            </button>

          </form>
        </div>
      </div>
    </div>
  );
}
