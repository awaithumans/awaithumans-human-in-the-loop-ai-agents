/**
 * Tests for `groupImageFields` — the pure helper that decides when
 * to collapse multiple image fields into a single carousel slot.
 *
 * The helper drives FormRenderer's rendering plan. Buggy grouping
 * either (a) hides image fields the reviewer needs to see (skipped
 * carousel emission) or (b) renders a noisy single-image carousel
 * when an inline image would be cleaner. Both regressions are
 * reviewer-visible, so the rules are pinned here.
 */

import { describe, expect, it } from "vitest";

import type { FormDefinition, ImageField } from "@/lib/form-types";

import { groupImageFields } from "./image-grouping";

function imageField(name: string, url: string): ImageField {
	return {
		kind: "image",
		name,
		label: null,
		hint: null,
		required: false,
		url,
		alt: null,
		width: null,
		height: null,
	};
}

function shortText(name: string): FormDefinition["fields"][number] {
	return {
		kind: "short_text",
		name,
		label: name,
		required: false,
		hint: null,
		placeholder: null,
		max_length: null,
		min_length: null,
		pattern: null,
		currency_code: null,
		subtype: "plain",
	} as unknown as FormDefinition["fields"][number];
}

describe("groupImageFields", () => {
	it("returns fields as-is when there are no image fields", () => {
		// Pure-form review — no document fragments at all (e.g. a
		// custom HITL task that isn't AwaitVerify). Must not wrap
		// anything in a carousel slot.
		const slots = groupImageFields([
			shortText("vendor"),
			shortText("amount"),
		]);
		expect(slots).toEqual([
			{ kind: "field", field: shortText("vendor") },
			{ kind: "field", field: shortText("amount") },
		]);
	});

	it("does NOT emit a carousel when there is exactly one image", () => {
		// The carousel chrome (prev/next buttons, "1 / 1" counter)
		// would be pure noise for a single-image form. Fall back to
		// the inline ImageDisplayRenderer.
		const fields = [imageField("doc_0", "http://x/0.png"), shortText("ok")];
		const slots = groupImageFields(fields);

		expect(slots).toHaveLength(2);
		expect(slots[0]).toEqual({ kind: "field", field: fields[0] });
		expect(slots[1]).toEqual({ kind: "field", field: fields[1] });
	});

	it("emits a single carousel slot when there are 2+ images", () => {
		// The canonical AwaitVerify case: multi-page document arrives
		// as N image fields. The reviewer should see one image at a
		// time with controls, not a scrolling stack.
		const img1 = imageField("doc_0", "http://x/0.png");
		const img2 = imageField("doc_1", "http://x/1.png");
		const fields = [img1, img2, shortText("vendor")];

		const slots = groupImageFields(fields);
		expect(slots).toHaveLength(2);
		expect(slots[0]).toEqual({
			kind: "carousel",
			images: [img1, img2],
		});
		expect(slots[1]).toEqual({ kind: "field", field: shortText("vendor") });
	});

	it("places the carousel at the FIRST image position", () => {
		// Field ordering matters — managed sometimes interleaves a
		// short_text introduction before the images. The carousel
		// should appear where the first image was, not at the top
		// of the form.
		const intro = shortText("intro");
		const img1 = imageField("doc_0", "http://x/0.png");
		const img2 = imageField("doc_1", "http://x/1.png");
		const trailing = shortText("vendor");
		const fields = [intro, img1, img2, trailing];

		const slots = groupImageFields(fields);
		expect(slots).toHaveLength(3);
		expect(slots[0]).toEqual({ kind: "field", field: intro });
		expect(slots[1]).toEqual({
			kind: "carousel",
			images: [img1, img2],
		});
		expect(slots[2]).toEqual({ kind: "field", field: trailing });
	});

	it("collects ALL image fields into the carousel, even if non-consecutive", () => {
		// If managed ever interleaves a text field BETWEEN two image
		// fields, the reviewer still expects all images grouped — a
		// single carousel for the whole document, not two separate
		// ones with an input wedged in.
		const img1 = imageField("doc_0", "http://x/0.png");
		const mid = shortText("note");
		const img2 = imageField("doc_1", "http://x/1.png");
		const fields = [img1, mid, img2];

		const slots = groupImageFields(fields);
		expect(slots).toHaveLength(2);
		expect(slots[0]).toEqual({
			kind: "carousel",
			images: [img1, img2],
		});
		// The middle text field keeps its relative position AFTER
		// the carousel — it was originally between the two images,
		// but the carousel absorbs both image positions and the
		// non-image field naturally lands after.
		expect(slots[1]).toEqual({ kind: "field", field: mid });
	});

	it("preserves image order inside the carousel", () => {
		// Reviewer mental model: page 1, page 2, page 3 …
		// Shuffling the order would force them to verify pages
		// against a random sequence. The helper must walk fields
		// linearly.
		const imgs = [
			imageField("page_0", "http://x/0.png"),
			imageField("page_1", "http://x/1.png"),
			imageField("page_2", "http://x/2.png"),
			imageField("page_3", "http://x/3.png"),
			imageField("page_4", "http://x/4.png"),
		];
		const slots = groupImageFields(imgs);

		expect(slots).toHaveLength(1);
		const slot = slots[0];
		expect(slot.kind).toBe("carousel");
		if (slot.kind === "carousel") {
			expect(slot.images.map((i) => i.name)).toEqual([
				"page_0",
				"page_1",
				"page_2",
				"page_3",
				"page_4",
			]);
		}
	});

	it("returns an empty array for an empty form", () => {
		// Degenerate but real: a brand-new form with no fields yet.
		// Helper must not throw on the empty input.
		expect(groupImageFields([])).toEqual([]);
	});
});
