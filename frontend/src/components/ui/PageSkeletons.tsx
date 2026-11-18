"use client";

import React from "react";
import Navbar from "@/components/Navbar";
import { Skeleton } from "@/components/ui/skeleton";

/* ==========================================================================
   1. TRADES PAGE SKELETON
   ========================================================================== */
export function TradesPageSkeleton() {
  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <Skeleton className="h-6 w-56 mb-1.5" />
            <Skeleton className="h-3 w-80" />
          </div>
          <Skeleton className="h-4 w-36" />
        </header>

        {/* 4 KPI Metric Cards */}
        <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-[#27272a] bg-[#18181b] p-4 space-y-2"
            >
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-7 w-32" />
            </div>
          ))}
        </section>

        {/* Filter Toolbar */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Skeleton className="h-8 w-16 rounded-md" />
            <Skeleton className="h-8 w-20 rounded-md" />
            <Skeleton className="h-8 w-20 rounded-md" />
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <Skeleton className="h-8 w-full sm:w-60 rounded" />
            <Skeleton className="h-8 w-8 rounded shrink-0" />
          </div>
        </div>

        {/* Trades Table Card */}
        <section className="mt-4 overflow-x-auto rounded-xl border border-[#27272a] bg-[#18181b]">
          <div className="p-4 border-b border-[#26262b] flex items-center justify-between">
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-3 w-20" />
          </div>
          <div className="p-4 space-y-3">
            {[1, 2, 3, 4, 5, 6].map((row) => (
              <div key={row} className="flex items-center gap-4 py-2">
                <Skeleton className="h-3.5 w-24 shrink-0" />
                <Skeleton className="h-4 w-14 rounded shrink-0" />
                <Skeleton className="h-3.5 w-20 shrink-0" />
                <Skeleton className="h-4 w-12 rounded shrink-0" />
                <Skeleton className="h-3.5 w-16 shrink-0" />
                <Skeleton className="h-3.5 w-16 shrink-0" />
                <Skeleton className="h-3.5 w-20 shrink-0" />
                <Skeleton className="h-3.5 w-16 shrink-0" />
                <Skeleton className="h-6 w-20 rounded ml-auto shrink-0" />
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

/* ==========================================================================
   2. STRATEGIES PAGE SKELETON
   ========================================================================== */
export function StrategiesPageSkeleton() {
  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <Skeleton className="h-6 w-44 mb-1.5" />
            <Skeleton className="h-3 w-72" />
          </div>
          <Skeleton className="h-4 w-36" />
        </header>

        {/* Action Toolbar */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Skeleton className="h-8 w-32 rounded-lg" />
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <Skeleton className="h-8 flex-1 sm:w-60 rounded" />
            <Skeleton className="h-8 w-24 rounded shrink-0" />
          </div>
        </div>

        {/* Strategy Pipeline Cards */}
        <section className="mt-5 space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-[#27272a] bg-[#18181b] p-4 space-y-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-2">
                  <div className="flex items-center gap-2.5">
                    <Skeleton className="h-4 w-48" />
                    <Skeleton className="h-4 w-12 rounded" />
                    <Skeleton className="h-4 w-20" />
                  </div>
                  <Skeleton className="h-3 w-64" />
                </div>
                <div className="flex items-center gap-2">
                  <Skeleton className="h-7 w-16 rounded" />
                  <Skeleton className="h-7 w-20 rounded" />
                  <Skeleton className="h-7 w-16 rounded" />
                </div>
              </div>
              <Skeleton className="h-14 w-full rounded-lg" />
            </div>
          ))}
        </section>
      </main>
    </div>
  );
}

/* ==========================================================================
   3. BACKTEST LAB SKELETON
   ========================================================================== */
export function BacktestPageSkeleton() {
  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <Skeleton className="h-6 w-36 mb-1.5" />
            <Skeleton className="h-3 w-80" />
          </div>
          <Skeleton className="h-4 w-36" />
        </header>

        {/* Simulation Setup Card */}
        <section className="mt-5 rounded-xl border border-[#27272a] bg-[#18181b] p-5">
          <Skeleton className="h-3.5 w-36 mb-4" />
          <div className="grid gap-3 sm:grid-cols-3 mb-3">
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-full rounded-lg" />
          </div>
          <Skeleton className="h-20 w-full rounded-lg mb-4" />
          <div className="flex items-center justify-between">
            <Skeleton className="h-9 w-44 rounded-lg" />
            <Skeleton className="h-3 w-28" />
          </div>
        </section>

        {/* 6 Simulation KPI Cards */}
        <section className="mt-4 grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-[#27272a] bg-[#18181b] p-4 space-y-2"
            >
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-6 w-24" />
            </div>
          ))}
        </section>

        {/* Trade Ledger Table Skeleton */}
        <section className="mt-4 rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
          <div className="p-3 border-b border-[#26262b] bg-[#151518] flex items-center justify-between">
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-3 w-16" />
          </div>
          <div className="p-4 space-y-3">
            {[1, 2, 3, 4, 5].map((row) => (
              <div key={row} className="flex items-center gap-4 py-1.5">
                <Skeleton className="h-4 w-12 rounded" />
                <Skeleton className="h-3.5 w-32" />
                <Skeleton className="h-3.5 w-20" />
                <Skeleton className="h-3.5 w-20" />
                <Skeleton className="h-3.5 w-16" />
                <Skeleton className="h-3.5 w-16" />
                <Skeleton className="h-3.5 w-24 ml-auto" />
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

/* ==========================================================================
   4. BROKER ACCOUNTS SKELETON
   ========================================================================== */
export function AccountsPageSkeleton() {
  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4">
          <div>
            <Skeleton className="h-6 w-40 mb-1.5" />
            <Skeleton className="h-3 w-80" />
          </div>
          <Skeleton className="h-7 w-36 rounded-lg self-start sm:self-auto" />
        </div>

        {/* Connect Broker Card */}
        <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 sm:p-6 mb-8 space-y-5">
          <div className="flex items-center gap-2 mb-2">
            <Skeleton className="h-4 w-4 rounded-full" />
            <Skeleton className="h-3.5 w-40" />
          </div>

          <div className="space-y-1.5">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-9 w-full rounded-lg" />
          </div>

          <div className="space-y-1.5">
            <Skeleton className="h-3 w-24" />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-16 w-full rounded-lg" />
              ))}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-9 w-full rounded-lg" />
            </div>
            <div className="space-y-1.5">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-9 w-full rounded-lg" />
            </div>
          </div>

          <div className="flex items-center justify-end pt-1">
            <Skeleton className="h-9 w-44 rounded-lg" />
          </div>
        </section>

        {/* Connected Accounts List Skeleton */}
        <section className="space-y-3">
          <div className="flex items-center justify-between pb-1">
            <Skeleton className="h-3.5 w-44" />
            <Skeleton className="h-3 w-28" />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="flex flex-col justify-between p-4 rounded-xl border border-[#27272a] bg-[#18181b] space-y-4"
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-4 w-4 rounded" />
                  </div>
                  <Skeleton className="h-3 w-48" />
                  <Skeleton className="h-2.5 w-36" />
                </div>
                <div className="pt-2 flex items-center justify-between">
                  <Skeleton className="h-3 w-14" />
                  <Skeleton className="h-7 w-20 rounded-md" />
                </div>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

/* ==========================================================================
   5. SETTINGS PAGE SKELETON
   ========================================================================== */
export function SettingsPageSkeleton() {
  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <div className="flex items-center gap-2.5">
              <Skeleton className="h-6 w-64 mb-1" />
              <Skeleton className="h-5 w-24 rounded" />
            </div>
            <Skeleton className="h-3 w-96 mt-1" />
          </div>
          <div className="flex items-center gap-3">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-9 w-36 rounded-lg" />
          </div>
        </header>

        {/* Swipeable Tab Navigation Skeleton */}
        <div className="mt-6 flex items-center gap-2 border-b border-[#26262b] pb-2 overflow-x-auto no-scrollbar whitespace-nowrap">
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-9 w-36 rounded-t-lg shrink-0" />
          ))}
        </div>

        {/* Settings Body Cards */}
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          {/* Card 1 */}
          <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
              <Skeleton className="h-3.5 w-36" />
              <Skeleton className="h-3 w-24" />
            </div>
            <div className="space-y-1.5">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-9 w-full rounded-lg" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Skeleton className="h-9 w-full rounded-lg" />
              <Skeleton className="h-9 w-full rounded-lg" />
            </div>
            <Skeleton className="h-16 w-full rounded-lg" />
          </section>

          {/* Card 2 */}
          <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
              <Skeleton className="h-3.5 w-44" />
              <Skeleton className="h-3 w-12" />
            </div>
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-full rounded-lg" />
          </section>

          {/* Card 3 (Preferences Full Width) */}
          <section className="md:col-span-2 rounded-xl border border-[#27272a] bg-[#18181b] p-5 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
              <div>
                <Skeleton className="h-3.5 w-56 mb-1" />
                <Skeleton className="h-3 w-80" />
              </div>
              <Skeleton className="h-3 w-28" />
            </div>
            <div className="grid gap-6 md:grid-cols-2">
              <div className="grid grid-cols-2 gap-3">
                <Skeleton className="h-24 w-full rounded-lg" />
                <Skeleton className="h-24 w-full rounded-lg" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Skeleton className="h-24 w-full rounded-lg" />
                <Skeleton className="h-24 w-full rounded-lg" />
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
