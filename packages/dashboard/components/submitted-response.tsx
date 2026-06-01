"use client";

/**
 * Read-only view of a task's submitted response.
 *
 * Three render paths:
 *
 *   1. **Redacted (AwaitVerify post-callback).** When
 *      ``responseRedactedAt`` is non-null, the server has cleared the
 *      response from the DB after delivering it to the customer's
 *      callback URL. The dashboard renders a "Response delivered"
 *      placeholder instead — the customer's process is the canonical
 *      destination for the data; the audit log retains the metadata.
 *
 *   2. **With form_definition (primary).** Mount the same
 *      ``FormRenderer`` the reviewer used to fill the form, with
 *      ``disabled={true}`` so every input is read-only. This stays
 *      in sync with the write side automatically — new field kinds
 *      land in one place and the read-back gets them for free.
 *
 *   3. **Without form_definition (fallback).** Some tasks predate
 *      form_definition (programmatic tasks created via raw
 *      ``await_human`` without a Pydantic response_schema) so a
 *      ``FormDefinition`` may be null on the wire. In that case we
 *      walk the response JSON recursively and render primitives
 *      inline, arrays as numbered groups, and objects as nested
 *      key/value pairs. The fallback path never emits
 *      "[object Object]" — that string should not appear anywhere
 *      a reviewer can see.
 */

import { FormRenderer } from "@/components/form-renderer";
import type { FormDefinition } from "@/lib/form-types";

type Props = {
	response: Record<string, unknown> | null;
	formDefinition: FormDefinition | null;
	/** ISO 8601 UTC string when redaction fired, else null. */
	responseRedactedAt: string | null;
};

export function SubmittedResponse({
	response,
	formDefinition,
	responseRedactedAt,
}: Props) {
	// Redacted path takes priority: when the customer's callback has
	// ACKed and the response column has been cleared, there is no
	// content to render — only a "delivered" placeholder with the
	// timestamp.
	if (responseRedactedAt) {
		return <DeliveredPlaceholder timestamp={responseRedactedAt} />;
	}
	// Defensive: if the caller mounts us with no response and no
	// redaction, render nothing rather than a confusing empty card.
	// The page-level wrapper already gates on either of these being
	// truthy, so this branch is only reachable via mis-use.
	if (!response) return null;

	if (formDefinition) {
		// Disabled mode: FormRenderer threads the prop to every
		// primitive's <input disabled />, producing a faithful
		// read-back of exactly what the reviewer saw + filled in.
		return (
			<FormRenderer
				form={formDefinition}
				value={response}
				onChange={noop}
				disabled
			/>
		);
	}
	return <RecursiveValue value={response} />;
}

/**
 * Placeholder shown in place of the structured read-back when the
 * server has redacted ``response`` after a successful callback
 * delivery. The timestamp is rendered in the reviewer's local
 * timezone via ``Intl.DateTimeFormat`` so they can read "I see this
 * was delivered at 3:47 PM" without doing UTC math.
 */
function DeliveredPlaceholder({ timestamp }: { timestamp: string }) {
	const local = formatLocal(timestamp);
	return (
		<div className="space-y-2">
			<div className="text-sm font-semibold text-white/80">
				Response delivered
			</div>
			<p className="text-sm text-white/60 leading-relaxed">
				The response was forwarded to the caller at{" "}
				<span className="text-white/80">{local}</span> and its content
				has been redacted for privacy. The audit log retains submission
				metadata.
			</p>
		</div>
	);
}

function formatLocal(iso: string): string {
	// Server returns ISO 8601 strings; Date can parse them. Fall back
	// to the raw string if the input is malformed — we'd rather show
	// the timestamp as-is than crash the page.
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return iso;
	return new Intl.DateTimeFormat(undefined, {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(date);
}

function noop() {
	// FormRenderer requires an onChange but in disabled mode every
	// primitive renderer suppresses user interaction, so this is a
	// pure type-shim. Inlined rather than imported so a future move
	// of this component doesn't drag a "utils" file.
}

// ── Fallback: recursive primitive renderer ─────────────────────────

/**
 * Walks a JSON value and renders it inline. Three real cases plus
 * a defensive last branch:
 *   - primitive (string / number / boolean / null) → readable text
 *   - array → indexed entries with the same recursive renderer
 *   - object → key/value pairs, recursing into the values
 *   - anything else (defensive: BigInt, etc., never present in
 *     server-parsed JSON) → JSON.stringify in a <pre> so the
 *     reviewer at least sees the raw shape rather than
 *     "[object Object]"
 */
function RecursiveValue({ value }: { value: unknown }) {
	if (value === null || value === undefined) {
		return <span className="text-white/30 italic">empty</span>;
	}
	if (typeof value === "boolean") {
		return (
			<span className={value ? "text-brand" : "text-red-400"}>
				{value ? "Yes" : "No"}
			</span>
		);
	}
	if (typeof value === "number" || typeof value === "string") {
		// Empty string is a meaningful value (reviewer explicitly
		// cleared the field); rendering it as a thin placeholder
		// makes it visible.
		if (value === "") return <span className="text-white/30 italic">empty</span>;
		return <span className="text-sm">{value}</span>;
	}
	if (Array.isArray(value)) {
		if (value.length === 0) {
			return <span className="text-white/30 italic">no items</span>;
		}
		return (
			<ol className="list-decimal list-inside space-y-1 pl-3 border-l-2 border-white/10">
				{value.map((item, i) => (
					<li key={i} className="text-sm">
						<RecursiveValue value={item} />
					</li>
				))}
			</ol>
		);
	}
	if (typeof value === "object") {
		const entries = Object.entries(value as Record<string, unknown>);
		if (entries.length === 0) {
			return <span className="text-white/30 italic">empty</span>;
		}
		return (
			<div className="space-y-2 pl-3 border-l-2 border-white/10">
				{entries.map(([key, v]) => (
					<div key={key} className="flex items-start gap-3">
						<span className="text-white/40 text-sm min-w-[120px] font-mono">
							{key}
						</span>
						<div className="text-sm flex-1">
							<RecursiveValue value={v} />
						</div>
					</div>
				))}
			</div>
		);
	}
	// Defensive: shapes that don't come from JSON (BigInt, Date,
	// Symbol). Real JSON-loaded payloads can't reach this branch,
	// but we'd rather show JSON-ish text than the literal
	// "[object Object]".
	return (
		<pre className="text-xs text-white/50 bg-white/5 p-2 rounded overflow-x-auto">
			{JSON.stringify(value, null, 2)}
		</pre>
	);
}
