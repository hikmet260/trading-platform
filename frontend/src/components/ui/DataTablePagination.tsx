"use client";

import React, { useState, useEffect } from "react";
import {
  Pagination,
  PaginationItem,
  PaginationPrevious,
  PaginationNext,
  PaginationEllipsis,
} from "@/components/ui/pagination";

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia(query);
    if (media.matches !== matches) {
      setMatches(media.matches);
    }
    const listener = () => setMatches(media.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [matches, query]);

  return matches;
}

export interface DataTablePaginationProps {
  currentPage: number;
  totalPages: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  itemName?: string;
  className?: string;
}

export function DataTablePagination({
  currentPage,
  totalPages,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [15, 25, 50, 100],
  itemName = "records",
  className = "",
}: DataTablePaginationProps) {
  const isMobile = useMediaQuery("(max-width: 640px)");
  const isTablet = useMediaQuery("(max-width: 768px)");

  if (totalItems === 0) return null;

  const startRecord = (currentPage - 1) * pageSize + 1;
  const endRecord = Math.min(currentPage * pageSize, totalItems);

  const getVisiblePages = () => {
    const delta = isMobile ? 1 : isTablet ? 1 : 2;
    const rangeWithDots: (number | string)[] = [];

    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }

    rangeWithDots.push(1);

    let startPage = Math.max(2, currentPage - delta);
    let endPage = Math.min(totalPages - 1, currentPage + delta);

    if (currentPage === 1) {
      endPage = Math.min(totalPages - 1, 1 + delta * 2);
    } else if (currentPage === totalPages) {
      startPage = Math.max(2, totalPages - delta * 2);
    } else {
      startPage = Math.max(2, Math.min(startPage, currentPage));
      endPage = Math.min(totalPages - 1, Math.max(endPage, currentPage));
    }

    if (startPage > 2) {
      rangeWithDots.push("...");
    }

    for (let i = startPage; i <= endPage; i++) {
      if (i !== 1 && i !== totalPages) {
        rangeWithDots.push(i);
      }
    }

    if (endPage < totalPages - 1) {
      rangeWithDots.push("...");
    }

    if (totalPages > 1) {
      rangeWithDots.push(totalPages);
    }

    return rangeWithDots;
  };

  return (
    <div
      className={`flex flex-col sm:flex-row items-center justify-end gap-3 sm:gap-6 px-4 py-3 border-t border-[#26262b] bg-[#18181b] ${className}`}
    >
      {/* Record Range Summary and Page Size Selector */}
      <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-zinc-400 font-sans w-full sm:w-auto">
        <span>
          Showing <strong className="text-zinc-200">{startRecord}</strong>–
          <strong className="text-zinc-200">{endRecord}</strong> of{" "}
          <strong className="text-zinc-200">{totalItems}</strong> {itemName}
        </span>

        {onPageSizeChange && (
          <div className="flex items-center gap-1.5 ml-auto sm:ml-0">
            <span className="hidden sm:inline text-zinc-500">Rows:</span>
            <select
              value={pageSize}
              aria-label="Rows per page"
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              className="rounded border border-[#27272a] bg-[#141416] px-2 py-1 text-xs text-zinc-300 outline-none cursor-pointer hover:border-zinc-500 focus:border-cyan-500"
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Right Side: Page Navigation Controls */}
      {totalPages > 1 && (
        <div className="w-full sm:w-auto flex items-center justify-end overflow-x-auto no-scrollbar">
          <Pagination className="justify-end gap-1">
            <PaginationPrevious
              onClick={() => onPageChange(Math.max(1, currentPage - 1))}
              disabled={currentPage === 1}
              size={isMobile ? "sm" : "default"}
            >
              {isMobile ? "Prev" : "Previous"}
            </PaginationPrevious>

            {getVisiblePages().map((page, index) =>
              page === "..." ? (
                <PaginationEllipsis key={`ellipsis-${index}`} />
              ) : (
                <PaginationItem
                  key={`page-${page}`}
                  isActive={page === currentPage}
                  onClick={() => onPageChange(page as number)}
                  size={isMobile ? "sm" : "default"}
                >
                  {page}
                </PaginationItem>
              )
            )}

            <PaginationNext
              onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
              disabled={currentPage === totalPages}
              size={isMobile ? "sm" : "default"}
            >
              {isMobile ? "Next" : "Next"}
            </PaginationNext>
          </Pagination>
        </div>
      )}
    </div>
  );
}
