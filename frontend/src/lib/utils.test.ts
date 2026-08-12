import { describe, expect, it } from "vitest";
import { formatMs, formatSeconds, formatWhen, groupByDay } from "./utils";

describe("formatMs", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatMs(null)).toBe("");
    expect(formatMs(undefined)).toBe("");
  });

  it("renders sub-second values in ms", () => {
    expect(formatMs(320)).toBe("320ms");
    expect(formatMs(999)).toBe("999ms");
  });

  it("renders values >=1000ms in seconds, one decimal", () => {
    expect(formatMs(1000)).toBe("1.0s");
    expect(formatMs(6888)).toBe("6.9s");
  });
});

describe("formatSeconds", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatSeconds(null)).toBe("");
    expect(formatSeconds(undefined)).toBe("");
  });

  it("renders sub-minute values with one decimal", () => {
    expect(formatSeconds(12.34)).toBe("12.3s");
  });

  it("renders minute-scale values as Xm Ys", () => {
    expect(formatSeconds(125)).toBe("2m 5s");
  });
});

describe("formatWhen", () => {
  it("returns empty string for null", () => {
    expect(formatWhen(null)).toBe("");
  });

  it("renders a recent timestamp as 'just now'", () => {
    expect(formatWhen(new Date().toISOString())).toBe("just now");
  });

  it("renders a timestamp minutes ago in minutes", () => {
    const tenMinutesAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    expect(formatWhen(tenMinutesAgo)).toBe("10m ago");
  });

  it("renders a timestamp hours ago in hours", () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 3_600_000).toISOString();
    expect(formatWhen(threeHoursAgo)).toBe("3h ago");
  });
});

describe("groupByDay", () => {
  it("buckets by calendar day, not a rolling 24h window", () => {
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    // 1 minute into today and 1 minute before it (= yesterday) — 2 minutes
    // apart in real time, but different calendar days.
    const justAfterMidnight = new Date(startOfToday.getTime() + 60_000).toISOString();
    const justBeforeMidnight = new Date(startOfToday.getTime() - 60_000).toISOString();

    const groups = groupByDay([
      { id: 1, created_at: justAfterMidnight },
      { id: 2, created_at: justBeforeMidnight },
    ]);

    expect(groups).toEqual([
      { label: "Today", items: [{ id: 1, created_at: justAfterMidnight }] },
      { label: "Yesterday", items: [{ id: 2, created_at: justBeforeMidnight }] },
    ]);
  });

  it("buckets anything older than yesterday, and a missing timestamp, as Earlier", () => {
    const lastWeek = new Date(Date.now() - 7 * 86_400_000).toISOString();
    const groups = groupByDay([
      { id: 1, created_at: lastWeek },
      { id: 2, created_at: null },
    ]);
    expect(groups).toEqual([
      { label: "Earlier", items: [{ id: 1, created_at: lastWeek }, { id: 2, created_at: null }] },
    ]);
  });

  it("omits empty buckets rather than returning them with no items", () => {
    const groups = groupByDay([{ id: 1, created_at: new Date().toISOString() }]);
    expect(groups.map((g) => g.label)).toEqual(["Today"]);
  });

  it("returns nothing for an empty list", () => {
    expect(groupByDay([])).toEqual([]);
  });
});
