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

	it("treats a long single-line description as rich (prose, not a giant heading)", () => {
		const longPlain =
			"Approve this refund request from the customer for their recent " +
			"order, confirm the item was returned to the warehouse, and check " +
			"there are no prior refunds on the account before deciding";
		expect(longPlain.length).toBeGreaterThan(120);
		expect(looksRich(longPlain)).toBe(true);
	});

	it("keeps a normal-length one-line title as a heading", () => {
		// ~110 chars: a long but reasonable headline still renders as a heading.
		const headline =
			"Approve the $4,000 refund for order 4821 and add a short reason for the decision either way please";
		expect(headline.length).toBeLessThanOrEqual(120);
		expect(looksRich(headline)).toBe(false);
	});
});
