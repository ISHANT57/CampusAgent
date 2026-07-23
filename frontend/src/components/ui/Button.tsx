import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger";
}

export function Button({ variant = "primary", className, ...props }: Props) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        variant === "primary" &&
          "bg-[var(--color-accent)] text-[#06121f] hover:bg-[var(--color-accent)]/90",
        variant === "ghost" &&
          "border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface-2)]",
        variant === "danger" &&
          "border border-[var(--color-bad)]/40 text-[var(--color-bad)] hover:bg-[var(--color-bad)]/10",
        className,
      )}
    />
  );
}
