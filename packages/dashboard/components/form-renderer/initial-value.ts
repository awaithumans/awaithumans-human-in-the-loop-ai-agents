/**
 * Build the form's starting state from a FormDefinition.
 *
 * Pure data transformation — no JSX, no React — so vitest can import
 * this directly without going through a JSX-aware loader. The
 * renderer's `index.tsx` re-exports it for callers.
 *
 * When the optional `prefill` is supplied (AwaitVerify Flow A /
 * Flow B), customer-supplied values win over per-field defaults.
 * Missing keys fall back to defaults. Nested groups
 * (object_group / repeatable_group / section_collapse) descend by
 * path so an outer `prefill.address = {city: "..."}` seeds the
 * inner `address.city` field.
 */

import type { FormDefinition, FormField } from "@/lib/form-types";

import type { FormValue } from "./types";

export function initialValueFor(
	form: FormDefinition,
	prefill?: Record<string, unknown> | null,
): FormValue {
	const out: FormValue = {};
	walk(form.fields, out, prefill ?? null);
	return out;
}

// Kinds that render UI but don't contribute a value to the response.
// Skipping them here keeps display-only primitives (text blocks, media,
// layout) out of the submitted response payload even when they have a
// `name`. Without this, e.g. a display_text named "intro" shows up as
// `{"intro": null}` in the human's response — misleading and pollutes
// the audit trail.
const NON_INPUT_KINDS = new Set([
	"display_text",
	"image",
	"video",
	"pdf_viewer",
	"html",
	"section",
	"divider",
]);

function walk(
	fields: FormField[],
	out: FormValue,
	prefill: Record<string, unknown> | null,
): void {
	for (const f of fields) {
		if (!f.name) continue;
		if (NON_INPUT_KINDS.has(f.kind)) continue;

		// section_collapse flattens children into the same scope — its
		// own name doesn't appear in the prefill object. Use the parent
		// prefill verbatim.
		if (f.kind === "section_collapse") {
			walk(f.fields, out, prefill);
			continue;
		}

		const seeded =
			prefill !== null && f.name in prefill ? prefill[f.name] : undefined;

		switch (f.kind) {
			case "switch":
				out[f.name] =
					typeof seeded === "boolean" ? seeded : (f.default ?? null);
				break;
			case "single_select":
				out[f.name] =
					typeof seeded === "string" ? seeded : (f.default ?? null);
				break;
			case "multi_select":
				out[f.name] = Array.isArray(seeded) ? seeded : (f.default ?? []);
				break;
			case "picture_choice":
				out[f.name] = Array.isArray(seeded) ? seeded : (f.default ?? []);
				break;
			case "slider":
				out[f.name] =
					typeof seeded === "number"
						? seeded
						: (f.default ?? (f.min + f.max) / 2);
				break;
			case "star_rating":
				out[f.name] = typeof seeded === "number" ? seeded : (f.default ?? 0);
				break;
			case "opinion_scale":
				out[f.name] =
					typeof seeded === "number" ? seeded : (f.default ?? null);
				break;
			case "date":
			case "datetime":
			case "time":
				out[f.name] =
					typeof seeded === "string" ? seeded : (f.default ?? null);
				break;
			case "ranking":
				out[f.name] = Array.isArray(seeded)
					? seeded
					: f.options.map((o) => o.value);
				break;
			case "table":
				// Tables aren't expected in AwaitVerify pre-fill (they
				// use repeatable_group instead), but seed verbatim when
				// provided — managed sends row-shaped objects.
				out[f.name] = Array.isArray(seeded) ? seeded : [];
				break;
			case "subform":
				out[f.name] = Array.isArray(seeded) ? seeded : [];
				break;
			case "object_group": {
				// Recursive: seed children from a nested prefill object.
				// If the prefill at this name isn't a plain object,
				// children fall back to their own defaults.
				const subPrefill =
					seeded && typeof seeded === "object" && !Array.isArray(seeded)
						? (seeded as Record<string, unknown>)
						: null;
				const groupOut: FormValue = {};
				walk(f.fields, groupOut, subPrefill);
				out[f.name] = groupOut;
				break;
			}
			case "repeatable_group": {
				// Each item in the seed array becomes a row. We recurse
				// into item_fields so a row that's missing a key still
				// gets that field's default — important when the
				// customer's extraction filled some columns but not
				// others.
				if (Array.isArray(seeded)) {
					out[f.name] = seeded.map((row) => {
						const rowOut: FormValue = {};
						walk(
							f.item_fields,
							rowOut,
							row && typeof row === "object" && !Array.isArray(row)
								? (row as Record<string, unknown>)
								: null,
						);
						return rowOut;
					});
				} else {
					out[f.name] = [];
				}
				break;
			}
			default:
				// Plain-value input kinds (short_text, long_text, rich_text,
				// file_upload, signature, date_range) start blank, or
				// seed verbatim from the prefill when present.
				out[f.name] = seeded !== undefined ? seeded : null;
		}
	}
}
