import type { Metadata } from "next";
import "./globals.css";
import AccessTokenControl from "@/components/AccessTokenControl";

export const metadata: Metadata = {
  title: "Trading Platform",
  description: "Multi-asset trading operations dashboard",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}<AccessTokenControl /></body></html>;
}
