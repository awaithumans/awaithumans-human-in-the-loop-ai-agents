/**
 * Tests for `initialValueFor` — the helper that builds the form's
 * starting state from a FormDefinition plus an optional pre-fill
 * payload (AwaitVerify Flow A / Flow B).
 *
 * Three properties under test:
 *   1. Without pre-fill, defaults match the pre-PR-C behavior.
 *   2. Flat pre-fill values seed top-level fields verbatim.
 *   3. Nested pre-fill descends into object_group / repeatable_group.
 *
 * The pre-fill contract: customer-supplied values win over per-field
 * defaults; missing keys fall back to defaults. An empty array
 * (`prefill.rows = []`) is still a valid value — it means "no rows"
 * which a reviewer can add to.
 */

import { describe, expect, it } from "vitest";

import type { FormDefinition, FormField } from "@/lib/form-types";

import { initialValueFor } from "./initial-value";

function form(fields: FormField[]): FormDefinition {
	return { version: 1, fields };
}

function shortText(name: string, required = false): FormField {
	return {
		kind: "short_text",
		name,
		label: name,
		required,
		hint: null,
		placeholder: null,
		max_length: null,
		min_length: null,
		pattern: null,
		currency_code: null,
		subtype: "plain",
	} as unknown as FormField;
}

function switchField(name: string, defaultValue: boolean | null = null): FormField {
	return {
		kind: "switch",
		name,
		label: name,
		required: false,
		hint: null,
		true_label: "Yes",
		false_label: "No",
		default: defaultValue,
	} as unknown as FormField;
}

function objectGroup(name: string, children: FormField[]): FormField {
	return {
		kind: "object_group",
		name,
		label: name,
		required: false,
		hint: null,
		fields: children,
	} as unknown as FormField;
}

function repeatableGroup(name: string, itemFields: FormField[]): FormField {
	return {
		kind: "repeatable_group",
		name,
		label: name,
		required: false,
		hint: null,
		item_fields: itemFields,
		min_items: null,
		max_items: null,
	} as unknown as FormField;
}

describe("initialValueFor — no pre-fill", () => {
	it("starts plain-input fields blank", () => {
		const result = initialValueFor(
			form([shortText("vendor"), shortText("notes")]),
		);
		expect(result).toEqual({ vendor: null, notes: null });
	});

	it("honors per-field defaults when pre-fill is omitted", () => {
		const result = initialValueFor(form([switchField("approved", true)]));
		expect(result).toEqual({ approved: true });
	});

	it("seeds object_group as a nested empty dict respecting child defaults", () => {
		const result = initialValueFor(
			form([
				objectGroup("address", [
					shortText("city"),
					shortText("zip"),
				]),
			]),
		);
		expect(result).toEqual({ address: { city: null, zip: null } });
	});

	it("seeds repeatable_group as an empty array", () => {
		const result = initialValueFor(
			form([repeatableGroup("rows", [shortText("sku")])]),
		);
		expect(result).toEqual({ rows: [] });
	});
});

describe("initialValueFor — pre-fill (AwaitVerify Flow A / Flow B)", () => {
	it("uses pre-fill value over the per-field default for primitives", () => {
		const result = initialValueFor(
			form([shortText("vendor"), switchField("approved", false)]),
			{ vendor: "Acme Corp", approved: true },
		);
		expect(result).toEqual({ vendor: "Acme Corp", approved: true });
	});

	it("falls back to default when a key is missing from pre-fill", () => {
		// Reviewer expectation: partial pre-fills don't blank out the
		// other fields. Customer extracted some columns, left others
		// for human judgment.
		const result = initialValueFor(
			form([shortText("vendor"), shortText("notes")]),
			{ vendor: "Acme Corp" }, // `notes` missing
		);
		expect(result).toEqual({ vendor: "Acme Corp", notes: null });
	});

	it("descends into object_group with a nested pre-fill object", () => {
		const result = initialValueFor(
			form([
				objectGroup("address", [shortText("city"), shortText("zip")]),
			]),
			{ address: { city: "Brooklyn", zip: "11201" } },
		);
		expect(result).toEqual({
			address: { city: "Brooklyn", zip: "11201" },
		});
	});

	it("partial object_group pre-fill leaves missing children blank", () => {
		// Customer's extraction got the city but not the zip — common
		// when an OCR pass partial-matches. The reviewer fills in zip.
		const result = initialValueFor(
			form([
				objectGroup("address", [shortText("city"), shortText("zip")]),
			]),
			{ address: { city: "Brooklyn" } },
		);
		expect(result).toEqual({
			address: { city: "Brooklyn", zip: null },
		});
	});

	it("expands repeatable_group pre-fill into pre-populated rows", () => {
		// The canonical Flow A surface: customer's extraction yields
		// N line items, each one a partially-or-fully-filled row.
		const result = initialValueFor(
			form([
				repeatableGroup("line_items", [shortText("sku"), shortText("qty")]),
			]),
			{
				line_items: [
					{ sku: "A-1", qty: "2" },
					{ sku: "B-9", qty: "1" },
				],
			},
		);
		expect(result).toEqual({
			line_items: [
				{ sku: "A-1", qty: "2" },
				{ sku: "B-9", qty: "1" },
			],
		});
	});

	it("repeatable_group row with missing keys falls back per-cell", () => {
		// Row 0 missing qty: reviewer sees A-1 with qty blank,
		// fills it in. Without this we'd render `qty: undefined`
		// which the field renderer treats as un-touched/blank
		// anyway, but the explicit null is the cleaner shape.
		const result = initialValueFor(
			form([
				repeatableGroup("line_items", [shortText("sku"), shortText("qty")]),
			]),
			{ line_items: [{ sku: "A-1" }] },
		);
		expect(result).toEqual({ line_items: [{ sku: "A-1", qty: null }] });
	});

	it("empty repeatable_group pre-fill stays an empty array", () => {
		// Distinct from "key missing entirely" — empty array means
		// "extraction found nothing to enumerate." Reviewer can
		// click + Add row.
		const result = initialValueFor(
			form([repeatableGroup("rows", [shortText("sku")])]),
			{ rows: [] },
		);
		expect(result).toEqual({ rows: [] });
	});

	it("non-array repeatable_group pre-fill defaults to []", () => {
		// Defensive: if the wire shape drifts (customer sent `null`
		// or a stray object for what should be an array), don't
		// crash — fall back to the empty-rows default. The reviewer
		// can still add rows.
		const result = initialValueFor(
			form([repeatableGroup("rows", [shortText("sku")])]),
			{ rows: null as unknown as Record<string, unknown>[] },
		);
		expect(result).toEqual({ rows: [] });
	});

	it("non-object object_group pre-fill defaults to child-default scope", () => {
		// Same defensive pattern at the object level — if the
		// customer's extraction had `address: "123 Main St"` (a
		// plain string instead of a nested dict), we don't try to
		// interpret it; children fall back to their own defaults.
		const result = initialValueFor(
			form([
				objectGroup("address", [shortText("city")]),
			]),
			{ address: "123 Main St" },
		);
		expect(result).toEqual({ address: { city: null } });
	});

	it("nested repeatable_group inside object_group descends correctly", () => {
		// Realistic nested shape: an invoice's `vendor` object
		// contains a `previous_orders` list. Pre-fill must descend
		// through both layers without losing values.
		const result = initialValueFor(
			form([
				objectGroup("vendor", [
					shortText("name"),
					repeatableGroup("previous_orders", [shortText("order_id")]),
				]),
			]),
			{
				vendor: {
					name: "Acme",
					previous_orders: [{ order_id: "O-1" }, { order_id: "O-2" }],
				},
			},
		);
		expect(result).toEqual({
			vendor: {
				name: "Acme",
				previous_orders: [{ order_id: "O-1" }, { order_id: "O-2" }],
			},
		});
	});

	it("null pre-fill behaves the same as no pre-fill at all", () => {
		// Non-AwaitVerify tasks carry `initial_response: null`. The
		// helper must not crash and must produce the same defaults.
		const withNull = initialValueFor(
			form([shortText("vendor")]),
			null,
		);
		const without = initialValueFor(form([shortText("vendor")]));
		expect(withNull).toEqual(without);
	});
});
