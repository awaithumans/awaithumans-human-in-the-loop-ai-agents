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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
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
				responseRedactedAt={null}
			/>,
		);
		expect(screen.queryByText("[object Object]")).toBeNull();
		expect(screen.getByText("Acme")).toBeTruthy();
		expect(screen.getByText("O-1")).toBeTruthy();
		expect(screen.getByText("O-2")).toBeTruthy();
	});
});

describe("SubmittedResponse — redacted (post-callback) path", () => {
	it("renders the 'Response delivered' placeholder when responseRedactedAt is set", () => {
		// The AwaitVerify post-callback case: the customer's process
		// got the response, the server cleared it from the DB, the
		// dashboard shows the user that delivery happened without
		// surfacing the actual content.
		render(
			<SubmittedResponse
				response={null}
				formDefinition={null}
				responseRedactedAt="2026-06-01T15:47:23Z"
			/>,
		);
		expect(screen.getByText("Response delivered")).toBeTruthy();
		expect(screen.getByText(/forwarded to the caller/i)).toBeTruthy();
		expect(screen.getByText(/redacted for privacy/i)).toBeTruthy();
	});

	it("does NOT render the structured read-back when redacted", () => {
		// Even if the response field is somehow still present in the
		// payload (wire shape drift, partial migration, etc.), the
		// redacted placeholder must take priority — otherwise the
		// "redacted" label and the actual content would both render
		// and the privacy guarantee is broken.
		render(
			<SubmittedResponse
				response={{ vendor: "Acme Corp", amount: 100 }}
				formDefinition={null}
				responseRedactedAt="2026-06-01T15:47:23Z"
			/>,
		);
		expect(screen.queryByText("Acme Corp")).toBeNull();
		expect(screen.queryByText("100")).toBeNull();
		expect(screen.getByText("Response delivered")).toBeTruthy();
	});

	it("falls back to the raw ISO string when the timestamp is malformed", () => {
		// Defensive: a malformed timestamp from a future server version
		// shouldn't crash the page. We render the raw string verbatim
		// so an operator debugging it can still see the value.
		render(
			<SubmittedResponse
				response={null}
				formDefinition={null}
				responseRedactedAt="not-an-iso-date"
			/>,
		);
		expect(screen.getByText("Response delivered")).toBeTruthy();
		expect(screen.getByText(/not-an-iso-date/)).toBeTruthy();
	});

	it("renders nothing when response is null and not redacted", () => {
		// Defensive: the page-level wrapper guards on
		// (response || response_redacted_at), so this branch is only
		// reachable via mis-use. Don't render an empty card.
		const { container } = render(
			<SubmittedResponse
				response={null}
				formDefinition={null}
				responseRedactedAt={null}
			/>,
		);
		// The component returns null — container has zero children.
		expect(container.firstChild).toBeNull();
	});
});

describe("SubmittedResponse — optimistic-redaction chain (PR H, O9)", () => {
	// These tests pin the submit-time behavior of the task page in a
	// focused way: take a task with a populated response, run the
	// applyOptimisticRedaction overlay (the same helper handleSubmit
	// uses), and mount SubmittedResponse with the result. The
	// reviewer's typed values must NOT survive into the rendered
	// DOM. This is what closes the shoulder-surf / screenshot
	// window between Submit and the server-side callback dispatch.

	it("typed values disappear from the DOM immediately after the overlay applies", async () => {
		const { applyOptimisticRedaction } = await import(
			"@/app/(dashboard)/task/optimistic-redact"
		);

		// Simulate "reviewer just typed a sensitive value and clicked Submit"
		// — a unique string we can grep for to prove it leaks nowhere.
		const SENSITIVE = "very-sensitive-value-PR-H-pin";
		const taskAfterSubmit = applyOptimisticRedaction({
			// Minimal Task shape — only the fields the renderer reads.
			// Cast through unknown to skip the full Task interface;
			// the helper's shape contract is exercised in
			// optimistic-redact.test.ts.
			id: "t-1",
			response: { secret: SENSITIVE },
		} as unknown as Parameters<typeof applyOptimisticRedaction>[0]);

		render(
			<SubmittedResponse
				response={taskAfterSubmit.response}
				formDefinition={null}
				responseRedactedAt={taskAfterSubmit.response_redacted_at}
			/>,
		);

		// Paranoid pin — walk the entire DOM and assert the
		// sensitive string isn't anywhere. Catches the case where
		// a sibling component still has a copy in state.
		expect(document.body.textContent).not.toContain(SENSITIVE);
		// And the placeholder is up.
		expect(screen.getByText("Response delivered")).toBeTruthy();
		expect(screen.getByText(/redacted for privacy/i)).toBeTruthy();
	});

	it("non-redact submit path still renders the typed values", async () => {
		// Counter-test: when the task does NOT carry the redaction
		// flag, the existing in-house team behavior is preserved.
		// SubmittedResponse without responseRedactedAt renders the
		// structured read-back as PR F (#171) shipped it.
		render(
			<SubmittedResponse
				response={{ secret: "in-house-team-keeps-seeing-this" }}
				formDefinition={null}
				responseRedactedAt={null}
			/>,
		);
		expect(
			screen.getByText("in-house-team-keeps-seeing-this"),
		).toBeTruthy();
		expect(screen.queryByText("Response delivered")).toBeNull();
	});
});
