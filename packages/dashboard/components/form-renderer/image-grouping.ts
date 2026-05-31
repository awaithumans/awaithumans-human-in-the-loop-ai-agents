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
