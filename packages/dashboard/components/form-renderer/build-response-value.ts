/**
 * Build the response payload to submit to `POST /api/tasks/{id}/complete`.
 *
 * Walks the form definition and the user's value object together,
 * dropping any field whose value is `null`/`undefined` AND whose
 * schema marks it as not required.
 *
 * Without this, an unfilled optional `short_text` field renders an
 * untouched value of `null` (the renderer initializes plain-input
 * kinds blank) and the dashboard sends `{"reason": null}` to the
 * server. That fails Pydantic validation against the common
 * `field: str = ""` shape — Pydantic accepts `""` but not `None` for
 * a non-Optional `str`. Omitting the key lets the server's Pydantic
 * schema apply its default ("" or whatever) instead of choking on a
 * wire payload the agent's schema couldn't have produced anyway.
 *
 * Required fields keep their `null` so server-side validation can
 * surface a clear error rather than silently dropping the violation.
 *
 * Subform / table rows are recursed into so a blank optional column
 * or nested field gets the same treatment per row.
 *
 * Kept in a `.ts` (not `.tsx`) file so it's testable without a JSX
 * vite plugin. The renderer's `index.tsx` re-exports it for callers.
 */

import type { FormDefinition, FormField } from "@/lib/form-types";

import type { FormValue } from "./types";

// Layout / display primitives that have a `name` for layout purposes
// but never contribute a value to the response. Mirrors the same set
// in `index.tsx`'s `walk()` — duplicated rather than shared because
// the constant is small and tying the two files together via an
// intermediate module would obscure intent more than it would help.
const NON_INPUT_KINDS = new Set([
	"display_text",
	"image",
	"video",
	"pdf_viewer",
	"html",
	"section",
	"divider",
]);

export function buildResponseValue(
	form: FormDefinition,
	value: FormValue,
): FormValue {
	return cleanLevel(form.fields, value);
}

function cleanLevel(fields: FormField[], value: FormValue): FormValue {
	const out: FormValue = {};
	for (const f of fields) {
		if (!f.name) continue;
		if (NON_INPUT_KINDS.has(f.kind)) continue;

		// section_collapse flattens — its child fields' names live at
		// the same level as the parent in `value`, matching how the
		// renderer's `walk()` builds the initial state.
		if (f.kind === "section_collapse") {
			Object.assign(out, cleanLevel(f.fields, value));
			continue;
		}

		const v = value[f.name];

		// The core rule: drop null on non-required fields so Pydantic
		// defaults apply server-side. Required fields keep null so
		// server-side validation can flag the violation explicitly.
		if (v == null && !f.required) {
			continue;
		}

		// subform: array of nested FormValues; recurse into each row.
		if (f.kind === "subform") {
			const rows = Array.isArray(v) ? v : [];
			out[f.name] = rows.map((row) =>
				cleanLevel(f.fields, (row ?? {}) as FormValue),
			);
			continue;
		}

		// table: array of column-keyed objects. Same shape as a flat
		// FormValue per row, so the per-column null-on-not-required
		// rule applies.
		if (f.kind === "table") {
			const rows = Array.isArray(v) ? v : [];
			out[f.name] = rows.map((row) =>
				cleanTableRow(f.columns, (row ?? {}) as FormValue),
			);
			continue;
		}

		// object_group: recurse into the nested sub-FormValue. The
		// group's `name` carries an object value; children's names live
		// inside that object. Drop-blank-optionals applies per child.
		if (f.kind === "object_group") {
			const sub =
				v && typeof v === "object" && !Array.isArray(v)
					? (v as FormValue)
					: {};
			out[f.name] = cleanLevel(f.fields, sub);
			continue;
		}

		// repeatable_group: array of nested FormValues; recurse per row
		// using the row shape (item_fields). Drop-blank-optionals
		// applies per row, per item field — important so an unfilled
		// optional column on row 3 doesn't fail server-side validation
		// just because the column was added by + Add row.
		if (f.kind === "repeatable_group") {
			const rows = Array.isArray(v) ? v : [];
			out[f.name] = rows.map((row) =>
				cleanLevel(f.item_fields, (row ?? {}) as FormValue),
			);
			continue;
		}

		out[f.name] = v;
	}
	return out;
}

function cleanTableRow(
	columns: { name: string; required: boolean }[],
	row: FormValue,
): FormValue {
	const out: FormValue = {};
	for (const col of columns) {
		const v = row[col.name];
		if (v == null && !col.required) continue;
		out[col.name] = v;
	}
	return out;
}
