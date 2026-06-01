/**
 * Regression: the submitted-response view must never show
 * "[object Object]".
 *
 * Pre-PR, `task/page.tsx` ran `String(value)` on each entry in
 * `task.response`. JavaScript coerces nested objects and arrays to
 * the literal string "[object Object]", which is what a reviewer
 * saw immediately after clicking Submit on any task with a
 * list[BaseModel] or nested-object response.
 *
 * These tests pin both render paths:
 *   1. With form_definition → FormRenderer in disabled mode, no
 *      "[object Object]" anywhere.
 *   2. Without form_definition (fallback) → recursive primitive
 *      renderer, also no "[object Object]".
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { FormDefinition, FormField } from "@/lib/form-types";

import { SubmittedResponse } from "./submitted-response";

afterEach(() => {
	cleanup();
});

// Compact field factories for fixture forms.
function shortText(name: string, label = name): FormField {
	return {
		kind: "short_text",
		name,
		label,
		required: false,
		hint: null,
		placeholder: null,
		max_length: null,
		min_length: null,
		pattern: null,
		currency_code: null,
		subtype: "plain",
	} as unknown as FormField;
}

function objectGroup(name: string, fields: FormField[]): FormField {
	return {
		kind: "object_group",
		name,
		label: name,
		required: false,
		hint: null,
		fields,
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

function form(fields: FormField[]): FormDefinition {
	return { version: 1, fields };
}

describe("SubmittedResponse — with form_definition (Option A path)", () => {
	it("does not render '[object Object]' for a nested object response", () => {
		// Canonical bug case: response has a nested address object.
		// Pre-fix this rendered as `[object Object]` next to the
		// "address" key.
		render(
			<SubmittedResponse
				response={{
					vendor: "Acme Corp",
					address: { city: "Brooklyn", zip: "11201" },
				}}
				formDefinition={form([
					shortText("vendor"),
					objectGroup("address", [
						shortText("city"),
						shortText("zip"),
					]),
				])}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		// The flat primitive lands as plain text via the form input.
		expect(screen.getAllByDisplayValue("Acme Corp").length).toBeGreaterThan(0);
		expect(screen.getAllByDisplayValue("Brooklyn").length).toBeGreaterThan(0);
		expect(screen.getAllByDisplayValue("11201").length).toBeGreaterThan(0);
	});

	it("does not render '[object Object]' for a list[BaseModel] response", () => {
		// The other canonical case: a repeatable_group response
		// where each row is itself an object. Pre-fix each row
		// rendered as "[object Object]".
		render(
			<SubmittedResponse
				response={{
					line_items: [
						{ sku: "A-1", qty: "2" },
						{ sku: "B-9", qty: "1" },
					],
				}}
				formDefinition={form([
					repeatableGroup("line_items", [
						shortText("sku"),
						shortText("qty"),
					]),
				])}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		// Each cell renders the value via the form input.
		expect(screen.getAllByDisplayValue("A-1").length).toBeGreaterThan(0);
		expect(screen.getAllByDisplayValue("B-9").length).toBeGreaterThan(0);
	});

	it("renders all primitive inputs in disabled mode", () => {
		// The "read-only" promise — inputs must not be editable
		// after the task is submitted.
		const { container } = render(
			<SubmittedResponse
				response={{ vendor: "Acme Corp" }}
				formDefinition={form([shortText("vendor")])}
			/>,
		);
		const inputs = container.querySelectorAll("input");
		expect(inputs.length).toBeGreaterThan(0);
		for (const input of inputs) {
			expect(input.disabled).toBe(true);
		}
	});
});

describe("SubmittedResponse — without form_definition (fallback path)", () => {
	it("does not render '[object Object]' for a nested object response", () => {
		// Task created without a form_definition (programmatic
		// await_human without a Pydantic schema). The recursive
		// renderer must handle nesting cleanly.
		render(
			<SubmittedResponse
				response={{
					vendor: "Acme Corp",
					address: { city: "Brooklyn", zip: "11201" },
				}}
				formDefinition={null}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		// Primitives are still readable in the fallback renderer.
		expect(screen.getByText("Acme Corp")).toBeTruthy();
		expect(screen.getByText("Brooklyn")).toBeTruthy();
	});

	it("does not render '[object Object]' for an array of objects", () => {
		render(
			<SubmittedResponse
				response={{
					line_items: [
						{ sku: "A-1", qty: "2" },
						{ sku: "B-9", qty: "1" },
					],
				}}
				formDefinition={null}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		expect(screen.getByText("A-1")).toBeTruthy();
		expect(screen.getByText("B-9")).toBeTruthy();
	});

	it("renders booleans as Yes/No in the fallback", () => {
		// The original code had a special boolean case that produced
		// "Yes" / "No"; the new recursive renderer preserves that
		// rather than emitting raw "true" / "false".
		render(
			<SubmittedResponse
				response={{ approved: true, rejected: false }}
				formDefinition={null}
			/>,
		);
		expect(screen.getByText("Yes")).toBeTruthy();
		expect(screen.getByText("No")).toBeTruthy();
	});

	it("renders null and empty values with a visible placeholder", () => {
		// A reviewer who cleared a field should see something other
		// than blank space — "empty" makes it clear the field was
		// touched but left empty, distinct from "the key wasn't
		// present at all" (which the response wouldn't carry).
		render(
			<SubmittedResponse
				response={{ note: null, optional_text: "" }}
				formDefinition={null}
			/>,
		);
		expect(screen.getAllByText("empty").length).toBe(2);
	});

	it("renders deeply-nested values without '[object Object]'", () => {
		// Defensive: a response with three levels of nesting (object
		// → array → object) was the worst-case for the old
		// String(value) coercion. Make sure the recursive renderer
		// walks all the way down.
		render(
			<SubmittedResponse
				response={{
					vendor: {
						name: "Acme",
						previous_orders: [
							{ order_id: "O-1", total: 100 },
							{ order_id: "O-2", total: 250 },
						],
					},
				}}
				formDefinition={null}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		expect(screen.getByText("Acme")).toBeTruthy();
		expect(screen.getByText("O-1")).toBeTruthy();
		expect(screen.getByText("O-2")).toBeTruthy();
	});
});
