/**
 * Tests for ``applyOptimisticRedaction`` — the client-side overlay
 * applied immediately after a successful submit on a task with
 * ``redact_response_after_submit=true``.
 *
 * The helper does three things that the rest of the page wires
 * together to make the submit→redacted transition instantaneous:
 *
 *   1. Clears ``response`` so any sibling component reading it
 *      sees no content to render.
 *   2. Stamps ``response_redacted_at`` so SubmittedResponse picks
 *      the "Response delivered" placeholder branch.
 *   3. Flips ``status`` to "completed" so the form-mount gate
 *      (``canSubmitResponse``) becomes false and the form
 *      unmounts. Without this the form would render alongside
 *      the placeholder, defeating the privacy guarantee.
 *
 * The companion ``SubmittedResponse`` tests in PR #172 pin the
 * render side: when ``responseRedactedAt`` is non-null, the
 * "Response delivered" placeholder takes priority — even if a
 * stale ``response`` is present. Together the two test files
 * cover the submit→redacted chain end-to-end.
 */

import { describe, expect, it } from "vitest";

import type { Task } from "@/lib/types";

import { applyOptimisticRedaction } from "./optimistic-redact";

function baseTask(overrides: Partial<Task> = {}): Task {
	return {
		id: "t-1",
		idempotency_key: "k-1",
		task: "Verify invoice",
		payload: null,
		payload_schema: {},
		response_schema: {},
		form_definition: null,
		task_metadata: null,
		initial_response: null,
		redact_response_after_submit: true,
		response_redacted_at: null,
		status: "in_progress",
		assign_to: null,
		assigned_to_email: null,
		assigned_to_user_id: null,
		assigned_to_display_name: null,
		assigned_to_slack_user_id: null,
		response: { vendor: "Acme Corp", amount: 100 },
		verifier_result: null,
		verification_attempt: 0,
		timeout_seconds: 900,
		redact_payload: false,
		created_at: "2026-06-01T12:00:00Z",
		updated_at: "2026-06-01T12:00:00Z",
		completed_at: null,
		timed_out_at: null,
		completed_by_email: null,
		completed_by_user_id: null,
		completed_by_display_name: null,
		completed_by_slack_user_id: null,
		completed_via_channel: null,
		...overrides,
	};
}

describe("applyOptimisticRedaction", () => {
	it("clears response so siblings can't render the typed values", () => {
		const result = applyOptimisticRedaction(baseTask());
		expect(result.response).toBeNull();
	});

	it("stamps response_redacted_at with the provided clock", () => {
		// Test-injectable clock so the timestamp assertion is exact.
		// In production the helper falls back to `new Date()` per
		// the function default.
		const clock = new Date("2026-06-01T15:47:23.000Z");
		const result = applyOptimisticRedaction(baseTask(), clock);
		expect(result.response_redacted_at).toBe("2026-06-01T15:47:23.000Z");
	});

	it("uses the current time when no clock is passed", () => {
		// Looser: we don't know the exact millisecond, but the stamp
		// should be a parseable ISO string close to "now". Pinning
		// at all (vs leaving it null) is what matters; the exact
		// value gets overwritten on the next loadTask anyway.
		const before = Date.now();
		const result = applyOptimisticRedaction(baseTask());
		const after = Date.now();

		expect(result.response_redacted_at).not.toBeNull();
		const parsed = new Date(result.response_redacted_at as string).getTime();
		expect(parsed).toBeGreaterThanOrEqual(before);
		expect(parsed).toBeLessThanOrEqual(after);
	});

	it("flips status to 'completed' so the form unmounts via canSubmitResponse", () => {
		// canSubmitResponse on the task page gates on !isTerminal.
		// Without this status flip, the form would render alongside
		// the redacted placeholder. The status change is the
		// load-bearing piece that removes the typed inputs from the
		// DOM in the same render tick.
		const result = applyOptimisticRedaction(baseTask({ status: "in_progress" }));
		expect(result.status).toBe("completed");
	});

	it("preserves all unrelated fields verbatim", () => {
		// The overlay must not silently drop fields the rest of the
		// page reads — task_metadata, assign_to, completed_by_*,
		// audit trail keys, etc. Tested explicitly because the
		// helper does a spread + targeted overwrites; an accidental
		// destructuring miss would silently lose state.
		const task = baseTask({
			task_metadata: { customer: "Acme" },
			assigned_to_email: "alice@example.com",
			task: "Original title",
			id: "id-preserved",
			idempotency_key: "key-preserved",
		});
		const result = applyOptimisticRedaction(task);
		expect(result.task_metadata).toEqual({ customer: "Acme" });
		expect(result.assigned_to_email).toBe("alice@example.com");
		expect(result.task).toBe("Original title");
		expect(result.id).toBe("id-preserved");
		expect(result.idempotency_key).toBe("key-preserved");
	});

	it("does not mutate the input task", () => {
		// Functional purity — the helper returns a new object and
		// leaves the input alone. Without this, callers that hold
		// onto the original (e.g. for an undo affordance, which the
		// brief explicitly rejects but defensive coding is cheap)
		// would silently see their data clobbered.
		const original = baseTask({ status: "in_progress" });
		const originalResponse = original.response;
		applyOptimisticRedaction(original);
		expect(original.status).toBe("in_progress");
		expect(original.response).toBe(originalResponse);
		expect(original.response_redacted_at).toBeNull();
	});

	it("re-applies cleanly even if response is already null", () => {
		// Defensive: idempotent. Calling the helper twice produces
		// the same shape (modulo timestamp drift). Matters because
		// the sticky-ref machinery on the page may run the overlay
		// inside loadTask AFTER handleSubmit's optimistic mutation
		// has already redacted the task.
		const once = applyOptimisticRedaction(baseTask());
		const twice = applyOptimisticRedaction(once);
		expect(twice.response).toBeNull();
		expect(twice.status).toBe("completed");
		expect(twice.response_redacted_at).not.toBeNull();
	});
});
