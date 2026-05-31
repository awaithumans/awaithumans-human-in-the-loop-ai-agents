/**
 * Unit tests for `buildResponseValue` — the wire-shaping step the
 * dashboard runs before posting a completion to the server.
 *
 * Pins the rule that motivated the helper:
 *   - blank (null/undefined) optional fields are DROPPED from the JSON
 *   - required fields with null are KEPT (so server-side validation
 *     surfaces a clear error rather than the dashboard silently
 *     swallowing the violation)
 *   - empty strings stay as empty strings (user explicitly cleared
 *     the field; "" is a meaningful value distinct from "untouched")
 *   - empty arrays stay as empty arrays (multi_select with nothing
 *     picked is a valid value, not "untouched")
 *   - section_collapse children are flat at the same FormValue level
 *   - subform / table rows recurse so the same rule applies per row
 */

import { describe, expect, it } from "vitest";

import type { FormDefinition } from "@/lib/form-types";

import { buildResponseValue } from "./build-response-value";
import type { FormValue } from "./types";

function form(fields: FormDefinition["fields"]): FormDefinition {
	return { version: 1, fields };
}

// Compact field factories — the unit tests only care about
// `kind`, `name`, `required`, and (where relevant) nested `fields`.
// The runtime checks at the FormField level are the responsibility
// of the discriminated-union parser elsewhere.
function shortText(name: string, required: boolean) {
	return {
		kind: "short_text",
		name,
		label: name,
		required,
		hint: null,
		placeholder: null,
		max_length: null,
		min_length: null,
		default: null,
		subtype: "plain",
	} as unknown as FormDefinition["fields"][number];
}

function multiSelect(name: string, required: boolean) {
	return {
		kind: "multi_select",
		name,
		label: name,
		required,
		hint: null,
		options: [],
		default: null,
	} as unknown as FormDefinition["fields"][number];
}

function aSwitch(name: string, required: boolean) {
	return {
		kind: "switch",
		name,
		label: name,
		required,
		hint: null,
		true_label: "Yes",
		false_label: "No",
		default: null,
	} as unknown as FormDefinition["fields"][number];
}

describe("buildResponseValue", () => {
	it("drops null on a non-required field", () => {
		const f = form([shortText("reason", false), aSwitch("approved", true)]);
		const value: FormValue = { reason: null, approved: true };

		expect(buildResponseValue(f, value)).toEqual({ approved: true });
	});

	it("keeps null on a required field so server can flag it", () => {
		const f = form([aSwitch("approved", true), shortText("reason", true)]);
		const value: FormValue = { approved: null, reason: null };

		expect(buildResponseValue(f, value)).toEqual({
			approved: null,
			reason: null,
		});
	});

	it("preserves empty string distinctly from null", () => {
		// User typing and clearing produces "". That's a meaningful
		// value (the human said "the answer is empty") versus null
		// ("untouched"). Send it as-is.
		const f = form([shortText("reason", false)]);
		expect(buildResponseValue(f, { reason: "" })).toEqual({ reason: "" });
	});

	it("preserves empty arrays for multi_select", () => {
		// "Nothing selected" is a valid completion for an optional
		// multi_select; don't drop it.
		const f = form([multiSelect("tags", false)]);
		expect(buildResponseValue(f, { tags: [] })).toEqual({ tags: [] });
	});

	it("drops undefined the same as null", () => {
		const f = form([shortText("reason", false)]);
		expect(buildResponseValue(f, { reason: undefined })).toEqual({});
	});

	it("flattens section_collapse children at the same level", () => {
		const collapse = {
			kind: "section_collapse",
			name: "advanced",
			label: "advanced",
			required: false,
			hint: null,
			title: "Advanced",
			subtitle: null,
			default_open: false,
			fields: [shortText("tier", false), shortText("ref", true)],
		} as unknown as FormDefinition["fields"][number];
		const f = form([aSwitch("approved", true), collapse]);

		const value: FormValue = {
			approved: true,
			tier: null,
			ref: null,
		};

		// `tier` (optional) is dropped; `ref` (required) is kept.
		expect(buildResponseValue(f, value)).toEqual({
			approved: true,
			ref: null,
		});
	});

	it("recurses into subform rows", () => {
		const subform = {
			kind: "subform",
			name: "items",
			label: "items",
			required: true,
			hint: null,
			min_count: null,
			max_count: null,
			initial_count: 1,
			add_label: "Add",
			remove_label: "Remove",
			fields: [shortText("sku", true), shortText("note", false)],
		} as unknown as FormDefinition["fields"][number];
		const f = form([subform]);

		const value: FormValue = {
			items: [
				{ sku: "A-1", note: "ok" },
				{ sku: "A-2", note: null },
			],
		};

		expect(buildResponseValue(f, value)).toEqual({
			items: [
				{ sku: "A-1", note: "ok" },
				{ sku: "A-2" },
			],
		});
	});

	it("recurses into table rows by column", () => {
		const table = {
			kind: "table",
			name: "amounts",
			label: "amounts",
			required: false,
			hint: null,
			min_rows: null,
			max_rows: null,
			initial_rows: 1,
			allow_add_row: true,
			allow_remove_row: true,
			columns: [
				{
					name: "currency",
					label: "currency",
					kind: "short_text",
					required: true,
					placeholder: null,
					options: null,
					currency_code: null,
					min_value: null,
					max_value: null,
					default: null,
				},
				{
					name: "memo",
					label: "memo",
					kind: "short_text",
					required: false,
					placeholder: null,
					options: null,
					currency_code: null,
					min_value: null,
					max_value: null,
					default: null,
				},
			],
		} as unknown as FormDefinition["fields"][number];
		const f = form([table]);

		const value: FormValue = {
			amounts: [
				{ currency: "USD", memo: null },
				{ currency: "EUR", memo: "" },
			],
		};

		expect(buildResponseValue(f, value)).toEqual({
			amounts: [{ currency: "USD" }, { currency: "EUR", memo: "" }],
		});
	});

	// ── Nested Pydantic groups (PR C, O3) ────────────────────────────

	it("assembles object_group children into a nested dict", () => {
		const group: FormDefinition["fields"][number] = {
			kind: "object_group",
			name: "address",
			label: "Address",
			required: false,
			hint: null,
			fields: [shortText("city", false), shortText("zip", true)],
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		const value: FormValue = {
			address: { city: "Brooklyn", zip: "11201" },
		};
		expect(buildResponseValue(f, value)).toEqual({
			address: { city: "Brooklyn", zip: "11201" },
		});
	});

	it("object_group drops blank optional child but keeps required null", () => {
		// Reviewer left an optional city blank and didn't fill required zip.
		// Optional city is dropped (server applies its Pydantic default);
		// required zip stays as null so Pydantic surfaces a clean
		// "missing required field" error.
		const group: FormDefinition["fields"][number] = {
			kind: "object_group",
			name: "address",
			label: "Address",
			required: false,
			hint: null,
			fields: [shortText("city", false), shortText("zip", true)],
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		const value: FormValue = {
			address: { city: null, zip: null },
		};
		expect(buildResponseValue(f, value)).toEqual({
			address: { zip: null },
		});
	});

	it("non-object value for object_group is treated as empty", () => {
		// Defensive: if the renderer ever puts a non-object at the
		// group's slot (e.g. an array via wire drift), we don't
		// crash — we assemble from an empty sub-scope. The required
		// city ends up with undefined in the output, which
		// JSON.stringify drops, so the server sees `{address: {}}`
		// and surfaces a clean "city missing" validation error.
		const group: FormDefinition["fields"][number] = {
			kind: "object_group",
			name: "address",
			label: "Address",
			required: false,
			hint: null,
			fields: [shortText("city", true)],
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		const value: FormValue = { address: ["unexpected"] };
		const result = buildResponseValue(f, value);
		// Wire shape — what the server actually receives after JSON.stringify.
		expect(JSON.parse(JSON.stringify(result))).toEqual({
			address: {},
		});
	});

	it("assembles repeatable_group rows as a list of cleaned dicts", () => {
		const group: FormDefinition["fields"][number] = {
			kind: "repeatable_group",
			name: "line_items",
			label: "Line items",
			required: false,
			hint: null,
			item_fields: [
				shortText("sku", true),
				shortText("memo", false),
			],
			min_items: null,
			max_items: null,
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		const value: FormValue = {
			line_items: [
				{ sku: "A-1", memo: null }, // optional memo dropped
				{ sku: "B-9", memo: "fast" }, // memo kept
				{ sku: null, memo: null }, // required sku stays null
			],
		};
		expect(buildResponseValue(f, value)).toEqual({
			line_items: [
				{ sku: "A-1" },
				{ sku: "B-9", memo: "fast" },
				{ sku: null },
			],
		});
	});

	it("empty repeatable_group serializes as an empty array", () => {
		// Reviewer added no rows. Distinct from "the column doesn't
		// exist" — sending `[]` lets the Pydantic schema's `default=[]`
		// validate cleanly when the field is non-nullable.
		const group: FormDefinition["fields"][number] = {
			kind: "repeatable_group",
			name: "rows",
			label: "Rows",
			required: false,
			hint: null,
			item_fields: [shortText("name", true)],
			min_items: null,
			max_items: null,
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		expect(buildResponseValue(f, { rows: [] })).toEqual({ rows: [] });
	});

	it("nested object_group inside repeatable_group row recurses correctly", () => {
		// Realistic shape: each line item carries a sub-address.
		// The walker descends both levels in one pass.
		const group: FormDefinition["fields"][number] = {
			kind: "repeatable_group",
			name: "shipments",
			label: "Shipments",
			required: false,
			hint: null,
			item_fields: [
				shortText("carrier", true),
				{
					kind: "object_group",
					name: "destination",
					label: "Destination",
					required: false,
					hint: null,
					fields: [shortText("city", true), shortText("zip", false)],
				} as unknown as FormDefinition["fields"][number],
			],
			min_items: null,
			max_items: null,
		} as unknown as FormDefinition["fields"][number];
		const f = form([group]);

		const value: FormValue = {
			shipments: [
				{
					carrier: "USPS",
					destination: { city: "Brooklyn", zip: null }, // optional zip drops
				},
			],
		};
		expect(buildResponseValue(f, value)).toEqual({
			shipments: [
				{ carrier: "USPS", destination: { city: "Brooklyn" } },
			],
		});
	});
});
