"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown } from "lucide-react";
import {
	fetchTask,
	fetchAuditTrail,
	fetchMe,
	completeTask,
	cancelTask,
	claimTask,
	deleteTask,
	type MeResponse,
	type Task,
	type AuditEntry,
} from "@/lib/server";
import { assigneeLabel, cn, completedByLabel, formatRelativeTime } from "@/lib/utils";
import {
	IDEMPOTENCY_KEY_DISPLAY_LENGTH,
	SECONDS_PER_MINUTE,
	TERMINAL_STATUSES,
} from "@/lib/constants";
import { CopyButton } from "@/components/copy-button";
import { ErrorBanner } from "@/components/error-banner";
import { Eyebrow } from "@/components/eyebrow";
import { NotificationFailureBanner } from "@/components/notification-failure-banner";
import { StatusBadge } from "@/components/status-badge";
import { TerminalSpinner } from "@/components/terminal-spinner";
import {
	buildResponseValue,
	FormRenderer,
	initialValueFor,
	type FormValue,
} from "@/components/form-renderer";
import { SubmittedResponse } from "@/components/submitted-response";

/**
 * Route is query-param (`/task?id=...`) rather than dynamic segment
 * (`/tasks/[id]`) so the dashboard can build to a flat static export
 * and ship inside the Python wheel. Dynamic segments without known
 * params at build time are incompatible with output: "export".
 *
 * `useSearchParams` requires a Suspense boundary in static export.
 */
export default function TaskDetailPage() {
	return (
		<Suspense fallback={<TerminalSpinner label="awaiting task" size="md" />}>
			<TaskDetailPageInner />
		</Suspense>
	);
}

function TaskDetailPageInner() {
	const router = useRouter();
	const searchParams = useSearchParams();
	const taskId = searchParams.get("id") ?? "";

	const [task, setTask] = useState<Task | null>(null);
	const [audit, setAudit] = useState<AuditEntry[]>([]);
	const [me, setMe] = useState<MeResponse | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const [formData, setFormData] = useState<FormValue>({});
	const [expandedAuditIds, setExpandedAuditIds] = useState<Set<string>>(
		new Set(),
	);

	const toggleAuditEntry = (id: string) => {
		setExpandedAuditIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	useEffect(() => {
		loadTask();
	}, [taskId]);

	const loadTask = async () => {
		try {
			setError(null);
			// Fetch /me alongside the task so we can decide whether to
			// show the "you're stepping in" banner without a second
			// round-trip after the form renders.
			//
			// Audit + /me are tolerant: a 403 on either (e.g. a
			// non-operator assignee whose audit perms regress, or a
			// momentary cookie blip) shouldn't blank the whole page
			// when the task itself loaded fine. Only the task fetch
			// failing should abort the render.
			const [taskData, auditData, meData] = await Promise.all([
				fetchTask(taskId),
				fetchAuditTrail(taskId).catch(() => []),
				fetchMe().catch(() => null),
			]);
			setTask(taskData);
			setAudit(auditData);
			setMe(meData);

			if (taskData.form_definition) {
				// Pass `initial_response` so AwaitVerify Flow A / Flow B
				// tasks mount the form with the customer's pre-computed
				// extraction populated. The reviewer verifies/corrects
				// rather than re-types. Non-AwaitVerify tasks have
				// `initial_response: null` and the form starts blank.
				setFormData(
					initialValueFor(taskData.form_definition, taskData.initial_response),
				);
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load task");
		} finally {
			setLoading(false);
		}
	};

	const handleSubmit = async () => {
		if (!task) return;
		setSubmitting(true);
		try {
			// Drop blank optional fields before sending so Pydantic
			// schemas with defaults (`field: str = ""`, etc.) apply
			// server-side instead of failing validation on a wire null.
			const responseBody = task.form_definition
				? buildResponseValue(task.form_definition, formData)
				: formData;
			await completeTask(task.id, {
				response: responseBody,
				completed_via_channel: "dashboard",
			});
			await loadTask();
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to submit");
		} finally {
			setSubmitting(false);
		}
	};

	const handleCancel = async () => {
		if (!task) return;
		try {
			await cancelTask(task.id);
			await loadTask();
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to cancel");
		}
	};

	const handleClaim = async () => {
		if (!task) return;
		try {
			await claimTask(task.id);
			// Reload — the task now has assigned_to_user_id == me.user_id,
			// so the response form starts rendering and the Claim button
			// hides itself.
			await loadTask();
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to claim task");
		}
	};

	const handleDelete = async () => {
		if (!task) return;
		const confirmed = window.confirm(
			`Delete this task? The row will be removed from the DB. ` +
				`Audit entries stay as a historical record.`,
		);
		if (!confirmed) return;
		try {
			await deleteTask(task.id);
			router.push("/");
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to delete");
		}
	};

	const isTerminal = task?.status
		? TERMINAL_STATUSES.includes(task.status)
		: false;

	// Operator stepping in: someone else owns this task, but the operator
	// is the omnipotent admin tier and can submit on their behalf. Surface
	// this in the UI so the audit trail's `completed_by_email` can be
	// reconciled with the assignee at a glance.
	const assigneeText = task ? assigneeLabel(task) : null;
	const steppingIn =
		!!me?.is_operator &&
		!isTerminal &&
		!!task &&
		((task.assigned_to_email !== null &&
			task.assigned_to_email !== me.email) ||
			(task.assigned_to_user_id !== null &&
				task.assigned_to_user_id !== me.user_id));

	// Claim is available when (a) the task is non-terminal, (b) no one
	// is assigned yet, and (c) the viewer is an operator (the only role
	// the server's /claim route accepts). The button hides itself once
	// a claim succeeds since `assigned_to_user_id` flips non-null on the
	// next loadTask() refresh.
	const canClaim =
		!!task &&
		!!me?.is_operator &&
		!isTerminal &&
		task.assigned_to_user_id === null &&
		task.assigned_to_email === null;

	// Whether to render the response form. Submitting requires the
	// viewer to be the assignee OR an operator (per `/complete` auth).
	// Without the `is_operator` branch, every operator who navigated to
	// an unassigned task — even one they JUST claimed — would see the
	// form disappear momentarily. With it, claimed-by-me tasks render
	// the form right away.
	const canSubmitResponse =
		!!task &&
		!isTerminal &&
		!!task.form_definition &&
		!!me &&
		(me.is_operator ||
			task.assigned_to_user_id === me.user_id ||
			task.assigned_to_email === me.email);

	if (loading) {
		return <TerminalSpinner label="awaiting task" size="md" />;
	}

	if (!task) {
		// Surface the underlying error when one is set — without this
		// any 4xx from the task fetch (auth blip, deleted task,
		// signed-link expiry) renders as a generic "not found",
		// which sent a Slack-only user in circles trying to figure
		// out why their fresh handoff URL didn't work.
		return (
			<div className="max-w-5xl mx-auto">
				{error && <ErrorBanner message={error} />}
				<div className="text-red-400 mt-4">
					{error ? "Couldn't load task." : "Task not found"}
				</div>
			</div>
		);
	}

	return (
		<div className="max-w-5xl mx-auto">
			{/* Header */}
			<div className="flex items-start justify-between mb-8">
				<div>
					<button
						type="button"
						onClick={() => router.push("/")}
						className="text-white/40 text-sm hover:text-white/60 transition-colors mb-2 block"
					>
						← Back to tasks
					</button>
					<h1 className="text-2xl font-bold">{task.task}</h1>
					<div className="flex items-center gap-3 mt-2">
						<StatusBadge status={task.status} />
						<span className="text-white/30 text-xs font-mono">{task.id}</span>
						<CopyButton code={task.id} />
					</div>
				</div>
				<div className="flex items-center gap-2">
					{canClaim && (
						<button
							type="button"
							onClick={handleClaim}
							className="px-3 py-1.5 text-sm rounded-md bg-brand text-black font-semibold hover:bg-brand/90 transition-colors"
						>
							Claim task
						</button>
					)}
					{!isTerminal && (
						<button
							type="button"
							onClick={handleCancel}
							className="px-3 py-1.5 text-sm rounded-md border border-red-400/30 text-red-400 hover:bg-red-400/10 transition-colors"
						>
							Cancel Task
						</button>
					)}
					<button
						type="button"
						onClick={handleDelete}
						className="px-3 py-1.5 text-sm rounded-md border border-white/15 text-white/50 hover:text-red-400 hover:border-red-400/40 hover:bg-red-400/5 transition-colors"
					>
						Delete
					</button>
				</div>
			</div>

			{error && <ErrorBanner message={error} />}

			<NotificationFailureBanner audit={audit} />

			<div className="grid grid-cols-3 gap-6">
				{/* Left: Payload + Response Form */}
				<div className="col-span-2 space-y-6">
					{/* Caller-supplied context. Renders above the payload so
					    reviewers see who/why before they read what. Hidden
					    when the agent didn't attach anything. */}
					{task.task_metadata &&
					Object.keys(task.task_metadata).length > 0 ? (
						<div className="border border-brand/20 rounded-lg p-5 bg-brand/[0.03]">
							<Eyebrow
								as="h2"
								size="md"
								tone="bright"
								weight="semibold"
								className="block mb-4"
							>
								Context
							</Eyebrow>
							<div className="space-y-3">
								{Object.entries(task.task_metadata).map(
									([key, value]) => (
										<div key={key} className="flex items-start gap-3">
											<span className="text-white/40 text-sm min-w-[120px] font-mono">
												{key}
											</span>
											<span className="text-sm break-all">{value}</span>
										</div>
									),
								)}
							</div>
						</div>
					) : null}

					{/* Payload */}
					<div className="border border-white/10 rounded-lg p-5">
						<Eyebrow as="h2" size="md" tone="bright" weight="semibold" className="block mb-4">
							Task Payload
						</Eyebrow>
						{task.payload && !task.redact_payload ? (
							<div className="space-y-3">
								{Object.entries(task.payload).map(([key, value]) => (
									<div key={key} className="flex items-start gap-3">
										<span className="text-white/40 text-sm min-w-[120px] font-mono">
											{key}
										</span>
										<span className="text-sm break-all">
											{typeof value === "string" && value.startsWith("http") ? (
												<a
													href={value}
													target="_blank"
													rel="noopener noreferrer"
													className="text-brand hover:underline"
												>
													{value}
												</a>
											) : (
												String(value)
											)}
										</span>
									</div>
								))}
							</div>
						) : task.redact_payload ? (
							<div className="text-white/30 text-sm italic">Payload redacted</div>
						) : (
							<div className="text-white/30 text-sm">No payload</div>
						)}
					</div>

					{/* Unassigned hint — only rendered for operators with claim eligibility,
					    so the viewer knows they need to claim before they can respond. */}
					{canClaim && (
						<div className="border border-amber-400/20 rounded-lg p-5 bg-amber-400/5">
							<div className="text-sm text-amber-200/90">
								This task is unassigned.{" "}
								<button
									type="button"
									onClick={handleClaim}
									className="underline hover:text-amber-100 transition-colors"
								>
									Claim it
								</button>{" "}
								to submit a response.
							</div>
						</div>
					)}

					{/* Response Form (only when the viewer is allowed to submit) */}
					{canSubmitResponse && (
						<div className="border border-brand/20 rounded-lg p-5 bg-brand/5">
							<Eyebrow as="h2" size="md" tone="brand" weight="semibold" className="block mb-4">
								Your Response
							</Eyebrow>
							{steppingIn && assigneeText && (
								<div className="mb-4 rounded-md border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200/90">
									<span className="font-mono text-amber-300">⚠</span>{" "}
									Assigned to{" "}
									<span className="font-mono">{assigneeText}</span>
									{" · "}you're submitting as operator
									{me?.email && (
										<span className="text-amber-200/60">
											{" "}
											({me.email})
										</span>
									)}
								</div>
							)}
							<FormRenderer
								// `canSubmitResponse` already gates on form_definition
								// being non-null; the type system doesn't carry that
								// across the boolean, hence the `!`.
								form={task.form_definition!}
								value={formData}
								onChange={setFormData}
								disabled={submitting}
							/>
							<button
								type="button"
								onClick={handleSubmit}
								disabled={submitting}
								className="mt-6 px-6 py-2.5 bg-brand text-black font-semibold text-sm rounded-md hover:bg-brand/90 disabled:opacity-50 transition-colors"
							>
								{submitting ? "Submitting..." : "Submit Response"}
							</button>
						</div>
					)}

					{/* Completed Response. Panel mounts when there's a
					    response OR a redaction stamp — the redacted
					    case has no `task.response` but still has a
					    "delivered" placeholder to render. */}
					{(task.response || task.response_redacted_at) && (
						<div className="border border-white/10 rounded-lg p-5">
							<Eyebrow as="h2" size="md" tone="bright" weight="semibold" className="block mb-4">
								{task.response_redacted_at ? "Response delivered" : "Response"}
							</Eyebrow>
							{/* SubmittedResponse owns the body branching:
							    redacted → placeholder card with the
							    local-time delivery stamp (no response
							    content); has form_definition →
							    FormRenderer disabled so nested fields
							    (object_group, repeatable_group) render
							    correctly; else → recursive fallback. */}
							<SubmittedResponse
								response={task.response}
								formDefinition={task.form_definition}
								responseRedactedAt={task.response_redacted_at}
							/>
							{completedByLabel(task) && (
								<div className="mt-4 text-white/30 text-xs">
									Completed by {completedByLabel(task)} via{" "}
									{task.completed_via_channel ?? "unknown"}{" "}
									{task.completed_at && formatRelativeTime(task.completed_at)}
								</div>
							)}
						</div>
					)}
				</div>

				{/* Right: Timeline */}
				<div className="border border-white/10 rounded-lg p-5">
					<Eyebrow as="h2" size="md" tone="bright" weight="semibold" className="block mb-4">
						Timeline
					</Eyebrow>
					{audit.length === 0 ? (
						<div className="text-white/30 text-sm">No events yet</div>
					) : (
						<div className="space-y-4">
							{audit.map((entry, i) => (
								<TimelineEntry
									key={entry.id}
									entry={entry}
									isLast={i === audit.length - 1}
									expanded={expandedAuditIds.has(entry.id)}
									onToggle={() => toggleAuditEntry(entry.id)}
								/>
							))}
						</div>
					)}

					{/* Task metadata */}
					<div className="mt-6 pt-4 border-t border-white/10 space-y-2 text-xs text-white/30">
						<div>
							<span className="text-white/50">Assigned to:</span>{" "}
							{assigneeText ? (
								<span className="font-mono text-white/70">{assigneeText}</span>
							) : (
								<span className="italic">unassigned</span>
							)}
						</div>
						<div>
							<span className="text-white/50">Timeout:</span>{" "}
							{Math.round(task.timeout_seconds / SECONDS_PER_MINUTE)} minutes
						</div>
						<div>
							<span className="text-white/50">Created:</span>{" "}
							{new Date(task.created_at).toLocaleString()}
						</div>
						<div className="flex items-center gap-1">
							<span className="text-white/50">Idempotency:</span>{" "}
							<span className="font-mono">
								{task.idempotency_key.slice(0, IDEMPOTENCY_KEY_DISPLAY_LENGTH)}...
							</span>
							<CopyButton code={task.idempotency_key} />
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}

// ─── Timeline entry ──────────────────────────────────────────────────

function TimelineEntry({
	entry,
	isLast,
	expanded,
	onToggle,
}: {
	entry: AuditEntry;
	isLast: boolean;
	expanded: boolean;
	onToggle: () => void;
}) {
	const hasDetails = entry.extra_data != null;
	const isNotificationFailure = entry.action === "notification_failed";
	const dotClass = isNotificationFailure
		? "bg-amber-500/20 border-amber-500"
		: entry.to_status === "completed"
			? "bg-brand/20 border-brand"
			: entry.to_status === "timed_out" ||
				  entry.to_status === "cancelled" ||
				  entry.to_status === "verification_exhausted"
				? "bg-red-400/20 border-red-400"
				: entry.to_status === "rejected"
					? "bg-amber-400/20 border-amber-400"
					: "bg-white/10 border-white/30";

	return (
		<div className="relative">
			{!isLast && (
				<div className="absolute left-[7px] top-5 bottom-0 w-px bg-white/10" />
			)}
			<div className="flex items-start gap-3">
				<div
					className={cn(
						"w-[15px] h-[15px] rounded-full border-2 mt-0.5 flex-shrink-0",
						dotClass,
					)}
				/>
				<div className="flex-1 min-w-0">
					{hasDetails ? (
						<button
							type="button"
							onClick={onToggle}
							className="flex items-center gap-1.5 text-left group"
							aria-expanded={expanded}
						>
							<span className="text-sm font-medium">{entry.action}</span>
							<ChevronDown
								className={cn(
									"w-3.5 h-3.5 text-white/30 transition-transform group-hover:text-white/60",
									expanded && "rotate-180",
								)}
							/>
						</button>
					) : (
						<div className="text-sm font-medium">{entry.action}</div>
					)}
					<div className="text-white/30 text-xs mt-0.5">
						{entry.actor_type === "human" && entry.actor_email
							? entry.actor_email
							: entry.actor_type}
						{entry.channel && ` via ${entry.channel}`}
					</div>
					<div className="text-white/20 text-xs mt-0.5">
						{formatRelativeTime(entry.created_at)}
					</div>
					{expanded && hasDetails && (
						<TimelineEntryDetails entry={entry} />
					)}
				</div>
			</div>
		</div>
	);
}

function TimelineEntryDetails({ entry }: { entry: AuditEntry }) {
	const data = entry.extra_data ?? {};
	const verifierReason =
		typeof data.verifier_reason === "string" ? data.verifier_reason : null;
	const verificationAttempt =
		typeof data.verification_attempt === "number"
			? data.verification_attempt
			: null;
	const responseKeys = Array.isArray(data.response_keys)
		? (data.response_keys as unknown[]).filter(
				(k): k is string => typeof k === "string",
			)
		: null;

	// Verifier-specific keys get dedicated UI; everything else in
	// extra_data falls through to the generic key-value list below.
	const VERIFIER_KEYS = new Set([
		"verifier_reason",
		"verification_attempt",
		"verifier_passed",
		"response_keys",
	]);
	const otherEntries = Object.entries(data).filter(
		([k]) => !VERIFIER_KEYS.has(k),
	);

	const showVerifierBlock =
		verifierReason !== null || verificationAttempt !== null;

	return (
		<div className="mt-3 space-y-3 text-xs">
			{showVerifierBlock && (
				<div className="rounded border border-amber-400/20 bg-amber-400/5 p-3 space-y-2">
					<div className="text-amber-400/80 font-medium">
						Verifier feedback
						{verificationAttempt !== null && (
							<span className="text-amber-400/50 font-normal">
								{" "}
								— attempt {verificationAttempt}
							</span>
						)}
					</div>
					{verifierReason && (
						<div className="text-white/70 whitespace-pre-wrap break-words">
							{verifierReason}
						</div>
					)}
					{responseKeys && responseKeys.length > 0 && (
						<div className="text-white/30">
							Submitted fields:{" "}
							<span className="font-mono text-white/50">
								{responseKeys.join(", ")}
							</span>
						</div>
					)}
				</div>
			)}
			{!showVerifierBlock && responseKeys && responseKeys.length > 0 && (
				<div className="rounded border border-white/10 p-3 text-white/30">
					Submitted fields:{" "}
					<span className="font-mono text-white/50">
						{responseKeys.join(", ")}
					</span>
				</div>
			)}
			{otherEntries.length > 0 && (
				<dl className="rounded border border-white/10 p-3 space-y-1.5">
					{otherEntries.map(([k, v]) => (
						<div key={k} className="flex gap-2">
							<dt className="text-white/40 font-mono">{k}:</dt>
							<dd className="text-white/70 font-mono break-all">
								{typeof v === "string" ? v : JSON.stringify(v)}
							</dd>
						</div>
					))}
				</dl>
			)}
		</div>
	);
}
