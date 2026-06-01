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

// ── derivePageLayout (AwaitVerify M5) ──────────────────────────────

import {
	derivePageLayout,
	flattenLayout,
	pageStartIndices,
} from "./image-grouping";

function pagedImage(
	name: string,
	url: string,
	page: number,
	frag: number,
	pageCount: number,
	fragmentsPerPage: number,
): ImageField {
	return {
		...imageField(name, url),
		page_index: page,
		fragment_in_page: frag,
		page_count: pageCount,
		fragments_per_page: fragmentsPerPage,
	};
}

describe("derivePageLayout", () => {
	it("returns flat for legacy fields without page metadata", () => {
		// Pre-M5 tasks: image fields carry no page_index. Render as
		// the original flat carousel — page selector chrome would
		// be noise.
		const images = [
			imageField("doc_0", "http://x/0.png"),
			imageField("doc_1", "http://x/1.png"),
			imageField("doc_2", "http://x/2.png"),
		];
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("flat");
		if (layout.kind === "flat") {
			expect(layout.images).toEqual(images);
		}
	});

	it("returns flat for empty input", () => {
		// Degenerate: no images at all. Must not throw.
		const layout = derivePageLayout([]);
		expect(layout.kind).toBe("flat");
		if (layout.kind === "flat") expect(layout.images).toEqual([]);
	});

	it("returns flat when metadata resolves to a single page", () => {
		// Defensive: managed shouldn't emit page_count: 1 (it would
		// just leave metadata off entirely), but a tools-side mistake
		// shouldn't break the dashboard.
		const images = [
			pagedImage("a", "http://x/0.png", 0, 0, 1, 5),
			pagedImage("b", "http://x/1.png", 0, 1, 1, 5),
		];
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("flat");
	});

	it("groups fragments by page_index when metadata is present", () => {
		// Canonical M5 surface: 3 pages × 5 fragments. Each page's
		// fragments live in the same bucket, sorted by
		// fragment_in_page.
		const images: ImageField[] = [];
		for (let p = 0; p < 3; p++) {
			for (let f = 0; f < 5; f++) {
				images.push(pagedImage(`p${p}f${f}`, `http://x/${p}-${f}.png`, p, f, 3, 5));
			}
		}
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("paged");
		if (layout.kind !== "paged") return;
		expect(layout.pageCount).toBe(3);
		expect(layout.fragmentsPerPage).toBe(5);
		expect(layout.pages.length).toBe(3);
		for (let p = 0; p < 3; p++) {
			expect(layout.pages[p].length).toBe(5);
			expect(layout.pages[p][0].name).toBe(`p${p}f0`);
			expect(layout.pages[p][4].name).toBe(`p${p}f4`);
		}
	});

	it("sorts within a page by fragment_in_page (defensive)", () => {
		// Managed emits fragments in order, but if the wire ever
		// arrives shuffled (proxy reorders, etc.) the carousel
		// nav must still see page 0 in the right order.
		const images = [
			pagedImage("frag4", "http://x/4.png", 0, 4, 1, 5),
			pagedImage("frag0", "http://x/0.png", 0, 0, 1, 5),
			pagedImage("frag2", "http://x/2.png", 0, 2, 1, 5),
			// Plus enough images on page 1 to keep it from collapsing
			// to flat.
			pagedImage("p1f0", "http://x/p1f0.png", 1, 0, 2, 5),
		];
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("paged");
		if (layout.kind !== "paged") return;
		// Page 0's fragments should be in 0, 2, 4 order — not their
		// original arrival order.
		const namesOnPage0 = layout.pages[0].map((i) => i.name);
		expect(namesOnPage0).toEqual(["frag0", "frag2", "frag4"]);
	});

	it("sorts pages by page_index ascending", () => {
		// Same as fragments within a page, but for the page list.
		// Reviewer expectation: page 1, page 2, page 3 — not the
		// order managed happened to emit them in.
		const images = [
			pagedImage("p2", "http://x/p2.png", 2, 0, 3, 5),
			pagedImage("p2b", "http://x/p2b.png", 2, 1, 3, 5),
			pagedImage("p0", "http://x/p0.png", 0, 0, 3, 5),
			pagedImage("p0b", "http://x/p0b.png", 0, 1, 3, 5),
			pagedImage("p1", "http://x/p1.png", 1, 0, 3, 5),
			pagedImage("p1b", "http://x/p1b.png", 1, 1, 3, 5),
		];
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("paged");
		if (layout.kind !== "paged") return;
		expect(layout.pages[0][0].name).toBe("p0");
		expect(layout.pages[1][0].name).toBe("p1");
		expect(layout.pages[2][0].name).toBe("p2");
	});

	it("uses max actual page length as fallback for fragmentsPerPage", () => {
		// Defensive: when fragments_per_page is missing from the
		// wire (it shouldn't be, but) the reported value should
		// at least match the biggest page seen.
		const images = [
			{
				...imageField("a", "http://x/0.png"),
				page_index: 0,
				fragment_in_page: 0,
				page_count: 2,
			},
			{
				...imageField("b", "http://x/1.png"),
				page_index: 0,
				fragment_in_page: 1,
				page_count: 2,
			},
			{
				...imageField("c", "http://x/2.png"),
				page_index: 1,
				fragment_in_page: 0,
				page_count: 2,
			},
		];
		const layout = derivePageLayout(images);
		expect(layout.kind).toBe("paged");
		if (layout.kind !== "paged") return;
		expect(layout.fragmentsPerPage).toBe(2);
	});
});

describe("flattenLayout + pageStartIndices", () => {
	it("flattens a paged layout to a single ordered list", () => {
		// The carousel uses a flat index across all fragments so
		// ← / → can step across page boundaries.
		const images: ImageField[] = [];
		for (let p = 0; p < 2; p++) {
			for (let f = 0; f < 3; f++) {
				images.push(pagedImage(`p${p}f${f}`, `http://x/${p}-${f}.png`, p, f, 2, 3));
			}
		}
		const layout = derivePageLayout(images);
		const flat = flattenLayout(layout);
		expect(flat.map((i) => i.name)).toEqual([
			"p0f0",
			"p0f1",
			"p0f2",
			"p1f0",
			"p1f1",
			"p1f2",
		]);
	});

	it("pageStartIndices returns the flat-index of each page's first fragment", () => {
		// pages of length [5, 5, 3] → start indices [0, 5, 10].
		const images: ImageField[] = [];
		for (let f = 0; f < 5; f++) images.push(pagedImage(`p0f${f}`, `http://x/0-${f}.png`, 0, f, 3, 5));
		for (let f = 0; f < 5; f++) images.push(pagedImage(`p1f${f}`, `http://x/1-${f}.png`, 1, f, 3, 5));
		for (let f = 0; f < 3; f++) images.push(pagedImage(`p2f${f}`, `http://x/2-${f}.png`, 2, f, 3, 5));
		const layout = derivePageLayout(images);
		expect(pageStartIndices(layout)).toEqual([0, 5, 10]);
	});

	it("pageStartIndices returns [0] for a flat layout", () => {
		const layout = derivePageLayout([imageField("a", "http://x/0.png")]);
		expect(pageStartIndices(layout)).toEqual([0]);
	});
});
