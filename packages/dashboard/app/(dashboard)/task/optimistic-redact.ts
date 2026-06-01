/**
 * Optimistic-redaction overlay applied client-side after a successful
 * submit on a task with ``redact_response_after_submit=true``.
 *
 * Why: the server-side redaction only fires after the webhook
 * dispatcher delivers the callback (PR #172 / O8). The reviewer's
 * Submit click is acknowledged within milliseconds, but the callback
 * delivery happens on the scheduler's tick. Between the two, the
 * dashboard previously kept the typed values visible — a
 * shoulder-surf / screenshot window that could span seconds.
 *
 * The overlay flips the local task to its eventual server-side
 * shape: status COMPLETED, response cleared, response_redacted_at
 * stamped at submit time. The render pipeline (SubmittedResponse +
 * canSubmitResponse) then renders the "Response delivered"
 * placeholder immediately. When the server catches up on the next
 * fetch, the server's response_redacted_at replaces ours.
 *
 * Kept as a pure function so vitest can unit-test it without
 * mounting the page.
 */

import type { Task } from "@/lib/types";

export function applyOptimisticRedaction(task: Task, now: Date = new Date()): Task {
	return {
		...task,
		// Clear the response so any sibling component reading
		// `task.response` (e.g. SubmittedResponse's structured
		// read-back) sees nothing to render.
		response: null,
		// Synthesize the timestamp client-side. The server-side value
		// (stamped when the customer's callback ACKs) will replace
		// this on the next loadTask fetch — the placeholder text
		// doesn't care about millisecond-level precision.
		response_redacted_at: now.toISOString(),
		// Flip to terminal so canSubmitResponse becomes false and the
		// form unmounts (rather than rendering alongside the
		// placeholder, which would defeat the privacy guarantee).
		status: "completed",
	};
}
