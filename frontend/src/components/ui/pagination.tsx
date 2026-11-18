"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { ChevronLeft, ChevronRight, MoreHorizontal } from "lucide-react";

const paginationVariants = cva("flex items-center justify-end select-none", {
  variants: {
    variant: {
      default: "gap-1",
      compact: "gap-0.5",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

const paginationItemVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-35 cursor-pointer",
  {
    variants: {
      variant: {
        default:
          "rounded-lg border border-[#27272a] bg-[#18181b] hover:bg-[#222227] text-zinc-300 hover:text-zinc-100",
        outline:
          "rounded-lg border border-[#27272a] bg-[#18181b] hover:bg-[#222227] text-zinc-300 hover:text-zinc-100",
        ghost:
          "rounded-lg text-zinc-400 hover:bg-[#222227] hover:text-zinc-200",
      },
      size: {
        default: "h-8 w-8 sm:h-9 sm:w-9 text-xs sm:text-sm",
        sm: "h-7 w-7 sm:h-8 sm:w-8 text-xs",
        lg: "h-9 w-9 sm:h-10 sm:w-10 text-sm",
      },
      state: {
        default: "",
        active:
          "!bg-cyan-500 !text-zinc-950 !border-cyan-500 font-semibold shadow-sm hover:!bg-cyan-400",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
      state: "default",
    },
  }
);

const paginationNavVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap text-xs sm:text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-35 rounded-lg px-2.5 sm:px-3 text-zinc-300 hover:bg-[#222227] hover:text-zinc-100 border border-[#27272a] bg-[#18181b] cursor-pointer",
  {
    variants: {
      size: {
        default: "h-8 sm:h-9",
        sm: "h-7 sm:h-8 text-xs px-2",
        lg: "h-9 sm:h-10 px-4",
      },
    },
    defaultVariants: {
      size: "default",
    },
  }
);

export interface PaginationProps
  extends React.HTMLAttributes<HTMLElement>,
    VariantProps<typeof paginationVariants> {}

export interface PaginationItemProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof paginationItemVariants> {
  isActive?: boolean;
}

export interface PaginationNavProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof paginationNavVariants> {}

export type PaginationEllipsisProps = React.HTMLAttributes<HTMLSpanElement>;

const Pagination = React.forwardRef<HTMLElement, PaginationProps>(
  ({ className, variant, ...props }, ref) => (
    <nav
      role="navigation"
      aria-label="pagination"
      className={cn(paginationVariants({ variant, className }))}
      ref={ref}
      {...props}
    />
  )
);
Pagination.displayName = "Pagination";

const PaginationItem = React.forwardRef<HTMLButtonElement, PaginationItemProps>(
  ({ className, variant, size, state, isActive, ...props }, ref) => (
    <button
      type="button"
      className={cn(
        paginationItemVariants({
          variant,
          size,
          state: isActive ? "active" : state,
          className,
        })
      )}
      ref={ref}
      aria-current={isActive ? "page" : undefined}
      {...props}
    />
  )
);
PaginationItem.displayName = "PaginationItem";

const PaginationPrevious = React.forwardRef<
  HTMLButtonElement,
  PaginationNavProps
>(({ className, size, children, ...props }, ref) => (
  <button
    type="button"
    className={cn(paginationNavVariants({ size, className }))}
    ref={ref}
    {...props}
  >
    <ChevronLeft className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" />
    <span>{children || "Previous"}</span>
  </button>
));
PaginationPrevious.displayName = "PaginationPrevious";

const PaginationNext = React.forwardRef<HTMLButtonElement, PaginationNavProps>(
  ({ className, size, children, ...props }, ref) => (
    <button
      type="button"
      className={cn(paginationNavVariants({ size, className }))}
      ref={ref}
      {...props}
    >
      <span>{children || "Next"}</span>
      <ChevronRight className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" />
    </button>
  )
);
PaginationNext.displayName = "PaginationNext";

const PaginationEllipsis = React.forwardRef<
  HTMLSpanElement,
  PaginationEllipsisProps
>(({ className, ...props }, ref) => (
  <span
    className={cn(
      "inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 text-zinc-500",
      className
    )}
    ref={ref}
    {...props}
  >
    <MoreHorizontal className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
    <span className="sr-only">More pages</span>
  </span>
));
PaginationEllipsis.displayName = "PaginationEllipsis";

export {
  Pagination,
  PaginationItem,
  PaginationPrevious,
  PaginationNext,
  PaginationEllipsis,
  paginationVariants,
  paginationItemVariants,
  paginationNavVariants,
};
