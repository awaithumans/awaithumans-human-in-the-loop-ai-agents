/**
 * Tests for `looksRich` — the heuristic that decides whether a task
 * string is rendered as Markdown prose or as a plain heading.
 *
 * False negatives are the regression that matters: a multi-paragraph
 * text-review brief that falls back to a single collapsed <h1> is
 * exactly the bad rendering this feature exists to fix. So the
 * positive cases (newlines + common Markdown syntax) are pinned here.
 */

import { describe, expect, it } from "vitest";

import { looksRich } from "./markdown";

describe("looksRich", () => {
	it("treats a short single-line title as plain", () => {
		expect(looksRich("Verify the invoice total")).toBe(false);
		expect(looksRich("Approve refund for order 4821")).toBe(false);
	});

	it("treats multi-line text as rich", () => {
		expect(looksRich("First paragraph.\n\nSecond paragraph.")).toBe(true);
	});

	it("detects common Markdown syntax", () => {
		expect(looksRich("Please review **carefully**")).toBe(true);
		expect(looksRich("# Heading")).toBe(true);
		expect(looksRich("- bullet one")).toBe(true);
		expect(looksRich("See [the docs](https://example.com)")).toBe(true);
		expect(looksRich("Run `await_review()`")).toBe(true);
	});

	it("does not over-trigger on apostrophes or money", () => {
		expect(looksRich("Verify John's ID")).toBe(false);
		expect(looksRich("Approve the $4,000 refund (urgent)")).toBe(false);
	});
});
