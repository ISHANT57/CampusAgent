import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders the status label", () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("animates the dot while running or connecting, not otherwise", () => {
    const { container: running } = render(<StatusBadge status="running" />);
    expect(running.querySelector(".animate-breathe")).not.toBeNull();

    const { container: connecting } = render(<StatusBadge status="connecting" />);
    expect(connecting.querySelector(".animate-breathe")).not.toBeNull();

    const { container: completed } = render(<StatusBadge status="completed" />);
    expect(completed.querySelector(".animate-breathe")).toBeNull();
  });

  it("falls back to the muted style for an unrecognised status", () => {
    // @ts-expect-error — deliberately an invalid status, to check the fallback.
    render(<StatusBadge status="not_a_real_status" />);
    expect(screen.getByText("not_a_real_status")).toHaveClass("text-[var(--color-muted)]");
  });
});
