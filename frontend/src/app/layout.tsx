import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import "./globals.css";
import { AuthProvider } from "@/context/AuthContext";
import { ToastProvider } from "@/context/ToastContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { ConfirmProvider } from "@/context/ConfirmContext";
import AuthGuard from "@/components/AuthGuard";
import CopilotWidget from "@/components/CopilotWidget";
import { TopProgressBar } from "@/components/ui/TopProgressBar";

export const metadata: Metadata = {
  title: "Trading Terminal",
  description: "High-performance institutional trading workspace",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="antialiased bg-[#121214] text-[#f4f4f5] font-sans">
        <Suspense fallback={null}>
          <TopProgressBar />
        </Suspense>
        <ToastProvider>
          <ThemeProvider>
            <ConfirmProvider>
              <AuthProvider>
                <AuthGuard>
                  {children}
                  <CopilotWidget />
                </AuthGuard>
              </AuthProvider>
            </ConfirmProvider>
          </ThemeProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
