/**
 * Temporal adapter — signal-based durable HITL.
 *
 * Two halves, deployed in two different processes:
 *
 *   1. **Inside a Temporal workflow** — call `awaitHuman(...)` from
 *      this module. We register a signal handler scoped to the task's
 *      idempotency key, fire an activity that POSTs the task to the
 *      awaithumans server, then `workflow.condition()` blocks on
 *      either the signal arriving OR a workflow timer — both cost
 *      zero compute under Temporal's "park the workflow" model.
 *
 *   2. **Inside the user's web server** — `dispatchSignal(...)` is
 *      the framework-agnostic helper they wrap in a route. It
 *      verifies the HMAC, parses the body, and signals the workflow
 *      back to life.
 *
 * Mirrors `awaithumans.adapters.temporal` (Python). Wire format and
 * signal-name derivation are identical so a TypeScript workflow can
 * hand off a webhook to a Python receiver and vice versa.
 *
 * @example Workflow-side
 * ```ts
 * import { awaitHuman } from "awaithumans/temporal";
 * import { z } from "zod";
 *
 * export async function refundWorkflow(amount: number) {
 *   const decision = await awaitHuman({
 *     task: "Approve refund?",
 *     payloadSchema: z.object({ amount: z.number() }),
 *     payload: { amount },
 *     responseSchema: z.object({ approved: z.boolean() }),
 *     timeoutMs: 15 * 60 * 1000,
 *     callbackUrl: "https://my-app.com/awaithumans/cb?wf=" + workflowInfo().workflowId,
 *     serverUrl: "http://localhost:3001",
 *     apiKey: process.env.AWAITHUMANS_ADMIN_API_TOKEN,
 *   });
 *   return decision.approved;
 * }
 * ```
 *
 * @example Callback-side (in your web server, NOT the workflow)
 * ```ts
 * import { Connection, Client } from "@temporalio/client";
 * import { dispatchSignal } from "awaithumans/temporal";
 *
 * const client = new Client({ connection: await Connection.connect() });
 *
 * app.post("/awaithumans/cb", async (req, res) => {
 *   const wf = req.query.wf as string;
 *   try {
 *     await dispatchSignal({
 *       temporalClient: client,
 *       workflowId: wf,
 *       body: req.rawBody,  // raw bytes, not parsed
 *       signatureHeader: req.header("x-awaithumans-signature"),
 *     });
 *     res.sendStatus(200);
 *   } catch (e) {
 *     if (e instanceof Error && e.message.includes("signature")) res.sendStatus(401);
 *     else res.sendStatus(400);
 *   }
 * });
 * ```
 *
 * Requires:
 *   npm install @temporalio/workflow @temporalio/client
 *   (peer dependencies — declared as optional in this SDK so the
 *    base install doesn't pull in Temporal for users who don't need
 *    it.)
 */

import { zodToJsonSchema } from "zod-to-json-schema";

import {
	SchemaValidationError,
	TaskCancelledError,
	TaskTimeoutError,
	VerificationExhaustedError,
} from "../../errors.js";
import { extractForm } from "../../forms/index.js";
import {
	serializeAssignTo,
	serializeVerifierConfig,
} from "../../internal/wire.js";
import type { AssignTo, AwaitHumanOptions, TaskStatus, VerifierConfig } from "../../types/index.js";
import { TERMINAL_STATUSES } from "../../types/index.js";

import type { ZodType as ZodTypeRef } from "zod";

// Static import of @temporalio/workflow.
//
// Why static (vs the lazy require this file used to do): Temporal's
// worker bundles the workflow file via webpack and resolves
// `@temporalio/workflow` as an external module — its sandbox VM
// provides the API at runtime, NOT a bundled copy. A `import("...")`
// expression (even with a literal) gets compiled to webpack
// require.ensure / `__webpack_require__.e`, which assumes a browser
// publicPath the VM doesn't have, and crashes with "Automatic
// publicPath is not supported in this browser."
//
// The trade-off: users importing `awaithumans/temporal` from a non-
// Temporal context now also load this file's transitive references,
// but those are tiny and tree-shake under any modern bundler. Users
// who never touch the temporal subpath aren't affected.
import * as wfApi from "@temporalio/workflow";

// Signal-name prefix — must match the Python adapter exactly.
// Cross-language receivers (Python web server signaling a TS
// workflow or vice versa) depend on this string being stable.
const SIGNAL_PREFIX = "awaithumans";

// Default timeout for the "create task" activity. Temporal retries
// activity failures automatically; this is just the per-attempt cap.
const DEFAULT_CREATE_ACTIVITY_TIMEOUT_MS = 30_000;

// ─── Workflow-side: awaitHuman ──────────────────────────────────────

export interface AwaitHumanTemporalOptions<TPayload, TResponse>
	extends AwaitHumanOptions<TPayload, TResponse> {
	/**
	 * URL where the awaithumans server should POST the completion
	 * webhook. The user's web server must host the callback receiver
	 * at this URL — see `dispatchSignal()` and the FastAPI/Express
	 * snippet in `examples/temporal/`.
	 */
	callbackUrl: string;

	/**
	 * Base URL of the awaithumans server (the human-facing one).
	 * In dev: `http://localhost:3001`. Required because workflows
	 * can't read environment variables directly — pass via input
	 * args or a config object.
	 */
	serverUrl: string;

	/** Bearer token for `serverUrl`. Same value the direct-mode SDK reads. */
	apiKey?: string;

	/** Per-attempt timeout for the create-task activity. Default: 30s. */
	createActivityTimeoutMs?: number;
}

interface CreateTaskActivityInput {
	serverUrl: string;
	apiKey: string | undefined;
	body: Record<string, unknown>;
}

interface CreateTaskActivityResult {
	id: string;
	idempotencyKey: string;
	// Echoed from the server so `awaitHuman` can short-circuit when an
	// idempotency-key collision returns an already-terminal task. PR
	// #72 — without this, a re-run with the same key would park the
	// workflow on `wf.condition` until timeout.
	status: string;
	response: unknown | null;
	completedAt: string | null;
	timedOutAt: string | null;
	verificationAttempt: number;
}

// The signal payload the user's web server delivers — mirrors the
// awaithumans webhook body exactly.
interface CompletionSignal {
	task_id?: string;
	idempotency_key?: string;
	status?: string;
	response?: unknown;
	completed_at?: string | null;
	timed_out_at?: string | null;
	completed_by_email?: string | null;
	completed_via_channel?: string | null;
	verification_attempt?: number;
}

/**
 * Map a server-returned terminal task to the right return value.
 * Used by both the idempotency-collision short-circuit (PR #72) and
 * the post-signal branches at the bottom of `awaitHuman` — only
 * difference is where the `response` field lives.
 *
 * Kept as a non-async function (no Temporal API touched) so it's
 * callable from the workflow sandbox without ceremony.
 */
function resolveTerminal<TResponse>(
	status: string,
	source: { response?: unknown; verificationAttempt?: number; verification_attempt?: number },
	responseSchema: ZodTypeRef<TResponse>,
	taskName: string,
	timeoutMs: number,
): TResponse {
	if (status === "completed") {
		const validated = responseSchema.safeParse(source.response);
		if (!validated.success) {
			throw new SchemaValidationError("response", validated.error.message);
		}
		return validated.data;
	}
	if (status === "timed_out") {
		throw new TaskTimeoutError(taskName, timeoutMs);
	}
	if (status === "cancelled") {
		throw new TaskCancelledError(taskName);
	}
	if (status === "verification_exhausted") {
		const attempt =
			source.verificationAttempt ?? source.verification_attempt ?? 0;
		throw new VerificationExhaustedError(taskName, attempt);
	}
	throw new Error(
		`Temporal adapter saw unknown terminal status '${status}' for task '${taskName}'`,
	);
}

function signalName(idempotencyKey: string): string {
	return `${SIGNAL_PREFIX}:${idempotencyKey}`;
}

/**
 * Awaitable: suspend the running Temporal workflow until a human
 * completes the task. See module docstring for the architecture.
 *
 * @throws TaskTimeoutError if `timeoutMs` elapses with no completion.
 * @throws TaskCancelledError if the task was cancelled (server side).
 * @throws VerificationExhaustedError if the verifier rejected every attempt.
 * @throws SchemaValidationError if the human's response doesn't match `responseSchema`.
 */
export async function awaitHuman<TPayload, TResponse>(
	options: AwaitHumanTemporalOptions<TPayload, TResponse>,
): Promise<TResponse> {
	// Use the statically-imported workflow API. See the import at
	// the top of this file for why static (Temporal's bundler treats
	// `@temporalio/workflow` as a sandbox-provided external).
	const wf = wfApi;

	// Default the idempotency key to the workflow ID — already unique
	// by construction and doesn't need a content hash. The previous
	// default reached for `generateIdempotencyKey`, which uses
	// `crypto.subtle.digest`; Temporal's workflow sandbox VM has no
	// `crypto` global, so the default crashed every first awaitHuman
	// call. Users can still override via `options.idempotencyKey`
	// when they want a content-derived key (e.g. de-duping retries
	// that share the same business identifier).
	const idempotencyKey =
		options.idempotencyKey ?? `temporal:${wf.workflowInfo().workflowId}`;
	const signal = signalName(idempotencyKey);

	// Captured by the closure below. We use a 1-element wrapper
	// because TypeScript doesn't let inner functions rebind outer
	// scalars without a class.
	const received: { value: CompletionSignal | null } = { value: null };

	const completionSignalDef = wf.defineSignal<[CompletionSignal]>(signal);
	wf.setHandler(completionSignalDef, (payload) => {
		received.value = payload;
	});

	const payloadJsonSchema = zodToJsonSchema(options.payloadSchema);
	const responseJsonSchema = zodToJsonSchema(options.responseSchema);
	const timeoutSeconds = Math.round(options.timeoutMs / 1000);
	// Derive form_definition from responseSchema so the dashboard can
	// render the Approve / Reject form when an operator opens or
	// claims the task. Direct-mode SDK does the same in
	// `await-human.ts`. Without this, dashboard-driven approval has
	// nothing to render.
	const formDefinition = extractForm(options.responseSchema);

	// Serialize the create-task wire body in the workflow (deterministic),
	// then ship it through an activity (where HTTP is allowed).
	const body = {
		task: options.task,
		payload: options.payload,
		payload_schema: payloadJsonSchema,
		response_schema: responseJsonSchema,
		form_definition: formDefinition,
		timeout_seconds: timeoutSeconds,
		idempotency_key: idempotencyKey,
		assign_to: serializeAssignTo(options.assignTo as AssignTo | undefined),
		notify: options.notify ?? null,
		verifier_config: serializeVerifierConfig(
			options.verifier as VerifierConfig | undefined,
		),
		redact_payload: options.redactPayload ?? false,
		callback_url: options.callbackUrl,
	};

	// Proxy the create-task activity through Temporal's activity stub.
	// `proxyActivities` returns a typed object whose methods, when
	// called, schedule the activity and return its result. The
	// activity itself is registered worker-side; users wire it up by
	// passing `awaithumansCreateTask` to `Worker.create({activities})`.
	const { awaithumansCreateTask } = wf.proxyActivities<{
		awaithumansCreateTask(
			input: CreateTaskActivityInput,
		): Promise<CreateTaskActivityResult>;
	}>({
		startToCloseTimeout:
			options.createActivityTimeoutMs ?? DEFAULT_CREATE_ACTIVITY_TIMEOUT_MS,
	});

	const taskRecord = await awaithumansCreateTask({
		serverUrl: options.serverUrl,
		apiKey: options.apiKey,
		body,
	});

	// Idempotency-collision short-circuit. If the server returned an
	// already-terminal task — e.g. a dev re-ran the example with the
	// same idempotency_key after a previous completion — there's no
	// signal coming. Pre-#72 the workflow parked on `wf.condition`
	// for the full `timeoutMs` and then threw `TaskTimeoutError`,
	// which made the example look broken on retry. Treat this as the
	// Stripe-style retry-returns-cached-response contract.
	if (TERMINAL_STATUSES.includes(taskRecord.status as TaskStatus)) {
		console.warn(
			`[awaithumans/temporal] idempotency_key=${idempotencyKey} already exists, ` +
				`status=${taskRecord.status}, completed_at=${taskRecord.completedAt ?? taskRecord.timedOutAt}. ` +
				`Returning cached response without waiting for a new signal. ` +
				`Pass a fresh idempotencyKey for a new attempt.`,
		);
		return resolveTerminal(
			taskRecord.status,
			taskRecord,
			options.responseSchema,
			options.task,
			options.timeoutMs,
		);
	}

	// Race: signal received OR timeout. `wf.condition` returns true
	// when the predicate fires, false when it timed out.
	const completed = await wf.condition(
		() => received.value !== null,
		options.timeoutMs,
	);
	if (!completed || received.value === null) {
		throw new TaskTimeoutError(options.task, options.timeoutMs);
	}

	const status = received.value.status;
	if (status === "completed") {
		const validated = options.responseSchema.safeParse(received.value.response);
		if (!validated.success) {
			throw new SchemaValidationError("response", validated.error.message);
		}
		return validated.data;
	}
	if (status === "timed_out") {
		throw new TaskTimeoutError(options.task, options.timeoutMs);
	}
	if (status === "cancelled") {
		throw new TaskCancelledError(options.task);
	}
	if (status === "verification_exhausted") {
		throw new VerificationExhaustedError(
			options.task,
			received.value.verification_attempt ?? 0,
		);
	}
	throw new Error(
		`Temporal adapter saw unknown terminal status '${status}' for task '${options.task}'`,
	);
}

/**
 * The activity that POSTs the task to the awaithumans server.
 *
 * Register this on your Temporal worker alongside any of your own
 * activities:
 *
 * ```ts
 * import { Worker } from "@temporalio/worker";
 * import { awaithumansCreateTask } from "awaithumans/temporal";
 *
 * const worker = await Worker.create({
 *   workflowsPath: require.resolve("./workflows"),
 *   activities: { ...myActivities, awaithumansCreateTask },
 *   taskQueue: "my-q",
 * });
 * ```
 *
 * Activities run OUTSIDE the workflow sandbox — `fetch` and the
 * Node stdlib are available here. Temporal's automatic retries
 * cover transient server failures.
 */
export async function awaithumansCreateTask(
	input: CreateTaskActivityInput,
): Promise<CreateTaskActivityResult> {
	const headers: Record<string, string> = {
		"Content-Type": "application/json",
	};
	if (input.apiKey) {
		headers["Authorization"] = `Bearer ${input.apiKey}`;
	}

	const url = `${input.serverUrl.replace(/\/$/, "")}/api/tasks`;
	const resp = await fetch(url, {
		method: "POST",
		headers,
		body: JSON.stringify(input.body),
	});

	if (resp.status !== 200 && resp.status !== 201) {
		const text = await resp.text();
		throw new Error(
			`awaithumans server rejected task creation (HTTP ${resp.status}): ${text.slice(
				0,
				500,
			)}`,
		);
	}

	// We need more than `id` + `idempotency_key` so the workflow can
	// short-circuit when an idempotency-key collision returns an
	// already-terminal task (PR #72). Fields that aren't yet set are
	// null on the wire.
	const data = (await resp.json()) as {
		id: string;
		idempotency_key: string;
		status: string;
		response: unknown | null;
		completed_at: string | null;
		timed_out_at: string | null;
		verification_attempt: number;
	};
	return {
		id: data.id,
		idempotencyKey: data.idempotency_key,
		status: data.status,
		response: data.response,
		completedAt: data.completed_at,
		timedOutAt: data.timed_out_at,
		verificationAttempt: data.verification_attempt,
	};
}

// ─── User-web-server-side: dispatchSignal ───────────────────────────

/**
 * Minimal interface satisfied by `Client` from `@temporalio/client`.
 * Typed as a structural interface so this module doesn't need to
 * import the heavy class — keeps `dispatchSignal` testable with a
 * fake.
 */
export interface TemporalClientLike {
	getHandle(workflowId: string): {
		signal(name: string, arg: unknown): Promise<void>;
	};
}

export interface DispatchSignalOptions {
	/** Connected `@temporalio/client` Client. */
	temporalClient: TemporalClientLike;

	/**
	 * The workflow ID to signal. Read from the request URL — typically
	 * a query param (`?wf=...`) the workflow encoded into
	 * `callbackUrl` when it called `awaitHuman`.
	 */
	workflowId: string;

	/** The raw request body bytes — needed for HMAC verification. */
	body: ArrayBuffer | Uint8Array | string;

	/** The `X-Awaithumans-Signature` header value (or null/undefined). */
	signatureHeader: string | null | undefined;

	/**
	 * The HMAC key used to verify the signature. Must match
	 * AWAITHUMANS_PAYLOAD_KEY on the awaithumans server (which
	 * derives the webhook key from it via HKDF). Most users read
	 * this from `process.env.AWAITHUMANS_PAYLOAD_KEY`.
	 *
	 * Cross-language note: the Python server uses HKDF with salt
	 * `b"awaithumans-webhook-v1"` and info `b"v1"` over PAYLOAD_KEY
	 * to derive the actual signing key. To verify here, do the same
	 * HKDF-SHA256 on the Node side; helpers below.
	 */
	payloadKey: string;
}

/**
 * Verify a webhook signature and signal the matching workflow.
 *
 * Wraps a few lines of route boilerplate so the user's web server
 * stays small. See module docstring for the Express snippet. Throws:
 *
 *   - `Error("Invalid awaithumans webhook signature.")` — wrap to 401
 *   - `Error("Webhook body is not JSON: ...")` — wrap to 400
 *   - `Error("Webhook missing idempotency_key: ...")` — wrap to 400
 */
export async function dispatchSignal(
	options: DispatchSignalOptions,
): Promise<void> {
	const bodyBytes = toUint8Array(options.body);
	const ok = await verifySignature({
		body: bodyBytes,
		signatureHeader: options.signatureHeader,
		payloadKey: options.payloadKey,
	});
	if (!ok) {
		throw new Error("Invalid awaithumans webhook signature.");
	}

	let payload: { idempotency_key?: string };
	try {
		payload = JSON.parse(new TextDecoder().decode(bodyBytes));
	} catch (cause) {
		throw new Error(`Webhook body is not JSON: ${(cause as Error).message}`);
	}
	const idem = payload.idempotency_key;
	if (typeof idem !== "string" || !idem) {
		throw new Error(
			`Webhook missing idempotency_key: ${JSON.stringify(payload)}`,
		);
	}

	const handle = options.temporalClient.getHandle(options.workflowId);
	await handle.signal(signalName(idem), payload);
}

// ─── HMAC verification (matches the Python server's HKDF derivation) ─

interface VerifySignatureInput {
	body: Uint8Array;
	signatureHeader: string | null | undefined;
	payloadKey: string;
}

async function verifySignature(input: VerifySignatureInput): Promise<boolean> {
	if (!input.signatureHeader) return false;
	const expected = await signBody(input.body, input.payloadKey);
	const supplied = input.signatureHeader.startsWith("sha256=")
		? input.signatureHeader
		: `sha256=${input.signatureHeader}`;
	return constantTimeEqual(expected, supplied);
}

/**
 * Compute the `sha256=<hex>` signature over `body`. Public so users
 * who want to drive the dispatch loop themselves can verify the
 * same way the SDK helper does.
 */
export async function signBody(
	body: Uint8Array,
	payloadKey: string,
): Promise<string> {
	const hkdfKey = await deriveHmacKey(payloadKey);
	// Web Crypto's BufferSource type narrowed in recent TS releases —
	// Uint8Array<ArrayBufferLike> isn't directly assignable. The cast
	// below is safe (it's the right runtime shape) and matches what
	// the runtime accepts.
	const cryptoKey = await crypto.subtle.importKey(
		"raw",
		hkdfKey as unknown as BufferSource,
		{ name: "HMAC", hash: "SHA-256" },
		false,
		["sign"],
	);
	const sig = await crypto.subtle.sign(
		"HMAC",
		cryptoKey,
		body as unknown as BufferSource,
	);
	return `sha256=${bytesToHex(new Uint8Array(sig))}`;
}

async function deriveHmacKey(payloadKey: string): Promise<Uint8Array> {
	// Mirror the Python server's HKDF parameters exactly. Salt and
	// info bytes are the source of truth for cross-language compat.
	//
	// PAYLOAD_KEY on the Python side is decoded from urlsafe-b64 to
	// 32 raw bytes BEFORE HKDF (see server/core/encryption.py:get_key).
	// We do the same here so a workflow signed in TS verifies on a
	// Python receiver and vice versa.
	const enc = new TextEncoder();
	const rawIkm = base64UrlDecode(payloadKey);
	const ikmRaw = await crypto.subtle.importKey(
		"raw",
		rawIkm as unknown as BufferSource,
		{ name: "HKDF" },
		false,
		["deriveBits"],
	);
	const derived = await crypto.subtle.deriveBits(
		{
			name: "HKDF",
			hash: "SHA-256",
			salt: enc.encode("awaithumans-webhook-v1") as unknown as BufferSource,
			info: enc.encode("v1") as unknown as BufferSource,
		},
		ikmRaw,
		32 * 8, // 32 bytes
	);
	return new Uint8Array(derived);
}

function constantTimeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let result = 0;
	for (let i = 0; i < a.length; i++) {
		result |= a.charCodeAt(i) ^ b.charCodeAt(i);
	}
	return result === 0;
}

function bytesToHex(bytes: Uint8Array): string {
	return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * URL-safe base64 decode that tolerates missing padding — matches
 * Python's `base64.urlsafe_b64decode(padded)` semantics. The
 * awaithumans server's PAYLOAD_KEY is typically generated via
 * `secrets.token_urlsafe(32)` which produces unpadded output.
 */
function base64UrlDecode(s: string): Uint8Array {
	// Restore padding to a multiple of 4.
	const padded = s + "=".repeat((4 - (s.length % 4)) % 4);
	// Convert URL-safe alphabet (`-_`) to standard (`+/`) before atob.
	const normalized = padded.replace(/-/g, "+").replace(/_/g, "/");
	const binary = atob(normalized);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes;
}

function toUint8Array(body: ArrayBuffer | Uint8Array | string): Uint8Array {
	if (body instanceof Uint8Array) return body;
	if (body instanceof ArrayBuffer) return new Uint8Array(body);
	return new TextEncoder().encode(body);
}

