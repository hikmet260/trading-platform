import React from "react";
import { cn } from "@/lib/utils";

export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "youtube-skeleton rounded-md",
        className
      )}
      {...props}
    />
  );
}
