"use client";

import React, { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { LoginPage as SplitSignInPage } from "@/components/ui/sign-in-page";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";

function LoginContent() {
  const { user, login, isLoading } = useAuth();
  const { toast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTarget = searchParams.get("redirect") || "/";

  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.replace(redirectTarget);
    }
  }, [user, isLoading, redirectTarget, router]);

  const handleSignIn = async (email: string, pass: string) => {
    setError("");
    setSubmitting(true);
    startTopLoader("Signing in...");
    try {
      const res = await login(email, pass, "local");
      if (res.success) {
        toast.success(`Welcome back, ${res.user?.email || email}!`, "Authentication Successful");
        router.replace(redirectTarget);
      } else {
        const errorMsg = res.error || "Authentication failed. Please check credentials.";
        setError(errorMsg);
        toast.error(errorMsg, "Sign In Failed");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to sign in.";
      setError(msg);
      toast.error(msg, "Error");
    } finally {
      setSubmitting(false);
      stopTopLoader();
    }
  };

  return (
    <SplitSignInPage
      onSignIn={handleSignIn}
      isLoading={submitting || isLoading}
      externalError={error}
      onNavigateSignUp={() => {
        toast.info("Registration is managed by your platform administrator.", "Operator Portal");
      }}
      onForgotPassword={() => {
        toast.info("Password resets are managed by your platform administrator.", "Password Reset");
      }}
    />
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#0b0c10] flex items-center justify-center text-zinc-400 font-mono text-xs">
          Loading...
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
