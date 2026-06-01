/**
 * Image grouping helper for the form renderer.
 *
 * When a form has 2+ top-level image fields (e.g. AwaitVerify document
 * fragments), we render them as a single carousel rather than a long
 * vertical stack the reviewer has to scroll through. With a single
 * image the form stays the way it is.
 *
 * `groupImageFields` walks the top-level field list ONCE and returns
 * a plan the renderer can execute without re-scanning. The plan
 * preserves field ordering: the carousel takes the position of the
 * first image field, and non-image fields keep their original index.
 *
 * Scope: only top-level image fields. Image fields nested inside a
 * `section_collapse` / `subform` / `repeatable_group` are left to
 * render individually — those are rare (review forms put document
 * fragments at the top level, not inside groupings) and the carousel
 * UX assumes the images are the primary visual content of the form.
 */

import type { FormField, ImageField } from "@/lib/form-types";

type CarouselSlot = {
	kind: "carousel";
	images: ImageField[];
};

type FieldSlot = {
	kind: "field";
	field: FormField;
};

export type RenderSlot = CarouselSlot | FieldSlot;

/**
 * Walk the field list and return the slots the renderer should emit
 * in order. With 0 or 1 image fields the result is just the input
 * fields wrapped as FieldSlot — no carousel slot is produced.
 *
 * With 2+ image fields, the carousel slot replaces the position of
 * the FIRST image and the remaining image fields are dropped from
 * the output (they live inside the carousel slot). Non-image fields
 * keep their original order.
 */
export function groupImageFields(fields: FormField[]): RenderSlot[] {
	const imageCount = fields.filter((f) => f.kind === "image").length;
	if (imageCount <= 1) {
		// 0 images: nothing to group.
		// 1 image: also no grouping — single-image forms keep their
		// inline image renderer. The carousel chrome (prev/next,
		// counter) would be noise here.
		return fields.map((field) => ({ kind: "field", field }));
	}

	const images = fields.filter(
		(f): f is ImageField => f.kind === "image",
	);

	const slots: RenderSlot[] = [];
	let carouselEmitted = false;
	for (const field of fields) {
		if (field.kind === "image") {
			// The first image slot is replaced by the carousel; the rest
			// are absorbed into it (no second emission).
			if (!carouselEmitted) {
				slots.push({ kind: "carousel", images });
				carouselEmitted = true;
			}
			continue;
		}
		slots.push({ kind: "field", field });
	}
	return slots;
}

// ── Page layout (AwaitVerify M5) ───────────────────────────────────
//
// When managed includes per-fragment page metadata (PR #M5), the
// carousel renders a two-level navigation: a page selector at the
// top, and the existing carousel scoped to one page's fragments
// inside it. ``derivePageLayout`` decides which layout to use and,
// for the paged case, returns the fragments grouped by page in a
// stable order.

export type FlatLayout = {
	kind: "flat";
	images: ImageField[];
};

export type PagedLayout = {
	kind: "paged";
	// Outer index is page (0-indexed). Inner array is the fragments
	// for that page, sorted by `fragment_in_page` ascending.
	pages: ImageField[][];
	pageCount: number;
	// Most-common case: every page has the same fragment count
	// (5 at v1). When pages have varying counts (e.g. a final page
	// with fewer fragments) this is the max; per-page actual count
	// is `pages[i].length`.
	fragmentsPerPage: number;
};

export type CarouselLayout = FlatLayout | PagedLayout;

/**
 * Decide the carousel layout for a set of image fields.
 *
 * Falls back to flat when:
 *   - No image carries page metadata (legacy task created before M5)
 *   - All images report ``page_count: 1`` (single-page document)
 *   - All images resolve to the same page (defensive — managed should
 *     not emit ``page_count > 1`` when only one page is present, but
 *     a tools-side mistake shouldn't break the dashboard)
 *
 * The metadata fields are optional on the wire (M5 is additive over
 * older tasks). Missing keys are treated as their pre-M5 defaults
 * (page_index=0, fragment_in_page=index, page_count=1) — pre-M5
 * tasks render in today's flat carousel without crashing on the
 * absent keys.
 */
export function derivePageLayout(images: ImageField[]): CarouselLayout {
	if (images.length === 0) {
		return { kind: "flat", images: [] };
	}
	// "Has page metadata" means at least one image carries page_index.
	// We could also key off page_count, but page_index is what drives
	// the grouping — defaulting to that signal keeps the logic crisp.
	const hasMetadata = images.some((img) => img.page_index !== undefined);
	if (!hasMetadata) {
		return { kind: "flat", images };
	}

	// Group images by their page_index. Missing page_index defaults
	// to 0 — partial metadata is unusual but we'd rather merge into
	// page 0 than crash.
	const buckets = new Map<number, ImageField[]>();
	for (const img of images) {
		const p = img.page_index ?? 0;
		const list = buckets.get(p) ?? [];
		list.push(img);
		buckets.set(p, list);
	}

	// Sort within each bucket by fragment_in_page (defensive — managed
	// emits in order, but client-side resilience is cheap).
	for (const list of buckets.values()) {
		list.sort(
			(a, b) => (a.fragment_in_page ?? 0) - (b.fragment_in_page ?? 0),
		);
	}

	// Materialize pages in ascending page_index order.
	const sortedPageIndices = [...buckets.keys()].sort((a, b) => a - b);
	const pages = sortedPageIndices.map((p) => buckets.get(p) ?? []);

	if (pages.length <= 1) {
		// Defensive: page metadata present but everything resolved to
		// page 0. Render as flat — the page selector would only show
		// "Page 1 of 1" which is noise.
		return { kind: "flat", images };
	}

	// fragments_per_page comes from any image that carries it; fall
	// back to the largest actual page length so the layout's reported
	// "expected" count matches reality even when the wire metadata
	// is stale.
	const declaredPerPage = images.find(
		(img) => img.fragments_per_page !== undefined,
	)?.fragments_per_page;
	const maxActualPerPage = Math.max(...pages.map((p) => p.length));
	const fragmentsPerPage = declaredPerPage ?? maxActualPerPage;

	return {
		kind: "paged",
		pages,
		pageCount: pages.length,
		fragmentsPerPage,
	};
}

/**
 * Flatten a layout to a single ordered list of images. The carousel
 * stores its navigation as a single flat index; this helper produces
 * the matching array so ← / → can step across page boundaries by
 * just incrementing the index.
 */
export function flattenLayout(layout: CarouselLayout): ImageField[] {
	return layout.kind === "paged" ? layout.pages.flat() : layout.images;
}

/**
 * Compute the flat-index of the FIRST fragment of each page. Used by
 * the page selector / PageDown / PageUp navigation: clicking page N
 * jumps the carousel's flat index to ``pageStarts[N]``.
 *
 * For a layout with pages of length [5, 5, 3], returns [0, 5, 10].
 * Always returns ``[0]`` for a flat layout.
 */
export function pageStartIndices(layout: CarouselLayout): number[] {
	if (layout.kind !== "paged") return [0];
	const starts: number[] = [0];
	let acc = 0;
	for (let i = 0; i < layout.pages.length - 1; i++) {
		acc += layout.pages[i].length;
		starts.push(acc);
	}
	return starts;
}
