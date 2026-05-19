"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
	fetchMe,
	fetchTasks,
	type MeResponse,
	type Task,
	type TaskStatus,
} from "@/lib/server";
import { assigneeLabel, formatRelativeTime } from "@/lib/utils";
import {
	SECONDS_PER_MINUTE,
	TASK_ID_TRUNCATE_LENGTH,
	TASK_LIST_DEFAULT_PAGE_SIZE,
	TASK_LIST_PAGE_SIZES,
	TASK_LIST_POLL_INTERVAL_MS,
} from "@/lib/constants";
import { ErrorBanner } from "@/components/error-banner";
import {
	TaskFilterBar,
	type StatusOption,
} from "@/components/filters/task-filter-bar";
import { ShellEmptyState } from "@/components/shell-empty-state";
import { StatusBadge } from "@/components/status-badge";
import { TerminalSpinner } from "@/components/terminal-spinner";

const STATUS_OPTIONS: readonly StatusOption[] = [
	{ label: "All", value: "all" },
	{ label: "Created", value: "created" },
	{ label: "Notified", value: "notified" },
	{ label: "Assigned", value: "assigned" },
	{ label: "In progress", value: "in_progress" },
	{ label: "Submitted", value: "submitted" },
	{ label: "Verified", value: "verified" },
	{ label: "Completed", value: "completed" },
	{ label: "Rejected", value: "rejected" },
	{ label: "Timed out", value: "timed_out" },
	{ label: "Cancelled", value: "cancelled" },
	{ label: "Verification exhausted", value: "verification_exhausted" },
] as const;

interface FilterState {
	status: TaskStatus | "all";
	assignedTo: string;
	unassigned: boolean;
	mine: boolean;
	pageSize: number;
	offset: number;
}

/**
 * URL-synced state. Each filter knob writes through to the query
 * string so refresh-and-share-a-link works. Reading the URL on every
 * render keeps the component a single source of truth — the URL.
 */
function readFiltersFromSearchParams(
	params: URLSearchParams,
): FilterState {
	const rawStatus = params.get("status") ?? "all";
	const status = STATUS_OPTIONS.some((o) => o.value === rawStatus)
		? (rawStatus as TaskStatus | "all")
		: "all";
	const rawSize = Number(params.get("pageSize") ?? TASK_LIST_DEFAULT_PAGE_SIZE);
	const pageSize = TASK_LIST_PAGE_SIZES.includes(rawSize)
		? rawSize
		: TASK_LIST_DEFAULT_PAGE_SIZE;
	const offset = Math.max(0, Number(params.get("offset") ?? "0") || 0);
	return {
		status,
		assignedTo: params.get("assignedTo") ?? "",
		unassigned: params.get("unassigned") === "true",
		mine: params.get("mine") === "true",
		pageSize,
		offset,
	};
}

function filtersToSearchParams(state: FilterState): URLSearchParams {
	const sp = new URLSearchParams();
	if (state.status !== "all") sp.set("status", state.status);
	if (state.assignedTo) sp.set("assignedTo", state.assignedTo);
	if (state.unassigned) sp.set("unassigned", "true");
	if (state.mine) sp.set("mine", "true");
	if (state.pageSize !== TASK_LIST_DEFAULT_PAGE_SIZE)
		sp.set("pageSize", String(state.pageSize));
	if (state.offset > 0) sp.set("offset", String(state.offset));
	return sp;
}

export default function TaskQueuePage() {
	// Suspense boundary required for `useSearchParams` under the
	// dashboard's static export build (matches the task detail page).
	return (
		<Suspense fallback={<TerminalSpinner label="awaiting tasks" size="md" />}>
			<TaskQueuePageInner />
		</Suspense>
	);
}

function TaskQueuePageInner() {
	const router = useRouter();
	const searchParams = useSearchParams();
	const filters = useMemo(
		() => readFiltersFromSearchParams(searchParams),
		[searchParams],
	);

	const [tasks, setTasks] = useState<Task[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [me, setMe] = useState<MeResponse | null>(null);

	const updateFilters = useCallback(
		(patch: Partial<FilterState>) => {
			// Mutating filters (status / mine / etc.) snaps offset back
			// to 0 — staying on page 5 of a different filter is rarely
			// what the user wants and breaks "Next is empty" detection.
			const offsetBumped = "offset" in patch && patch.offset !== undefined;
			const next: FilterState = {
				...filters,
				...patch,
				offset: offsetBumped ? (patch.offset as number) : 0,
			};
			const sp = filtersToSearchParams(next);
			const query = sp.toString();
			// `replace` so the browser's back button doesn't accumulate
			// a per-filter-tweak history entry.
			router.replace(query ? `/?${query}` : "/", { scroll: false });
		},
		[filters, router],
	);

	const loadTasks = useCallback(async () => {
		try {
			setError(null);
			const params: Parameters<typeof fetchTasks>[0] = {
				limit: filters.pageSize,
				offset: filters.offset,
			};
			if (filters.status !== "all") params.status = filters.status;
			if (filters.unassigned) {
				params.unassigned = true;
			} else if (filters.mine && me?.email) {
				params.assigned_to = me.email;
			} else if (filters.assignedTo) {
				params.assigned_to = filters.assignedTo;
			}
			const data = await fetchTasks(params);
			setTasks(data);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load tasks");
		} finally {
			setLoading(false);
		}
	}, [filters, me?.email]);

	// Fetch /me once; needed for "Mine only" → assigned_to=me.email.
	useEffect(() => {
		fetchMe()
			.then(setMe)
			.catch(() => setMe(null));
	}, []);

	useEffect(() => {
		loadTasks();
		const interval = setInterval(loadTasks, TASK_LIST_POLL_INTERVAL_MS);
		return () => clearInterval(interval);
	}, [loadTasks]);

	const currentPage = Math.floor(filters.offset / filters.pageSize) + 1;
	// We don't know the total — Next is disabled when fewer than a
	// full page came back (the server can't possibly have more rows
	// after this offset under the same filters).
	const hasNextPage = tasks.length === filters.pageSize;

	const filtersActive =
		filters.status !== "all" ||
		filters.assignedTo !== "" ||
		filters.unassigned ||
		filters.mine;

	return (
		<div>
			<div className="flex items-center justify-between mb-4">
				<div>
					<h1 className="text-2xl font-bold">Tasks</h1>
					<p className="text-white/40 text-sm mt-1">
						{loading
							? "loading…"
							: `${tasks.length} on this page` +
								(filtersActive ? " (filtered)" : "")}
					</p>
				</div>
			</div>

			<TaskFilterBar
				filters={{
					status: filters.status,
					assignedTo: filters.assignedTo,
					unassigned: filters.unassigned,
					mine: filters.mine,
				}}
				onChange={updateFilters}
				isOperator={!!me?.is_operator}
				statusOptions={STATUS_OPTIONS}
			/>

			{error && <ErrorBanner message={error} />}

			{loading ? (
				<TerminalSpinner label="awaiting tasks" size="md" />
			) : tasks.length === 0 ? (
				<ShellEmptyState
					heading="await_human — waiting for your first task"
					note="Save as refund.py / refund.ts, run it. A task will appear here — review and complete it in the dashboard to unblock your agent."
					snippet={{
						python: `from awaithumans import await_human_sync
from pydantic import BaseModel

class WireTransfer(BaseModel):
    transfer_id: str
    amount: float
    to: str

class Decision(BaseModel):
    approved: bool
    reason: str  # short-answer field — the reviewer explains their call

transfer_id = "WT-2026-0042"

print("→ creating task — go to the dashboard to review and complete it")

result = await_human_sync(
    task="Approve this wire transfer",
    payload_schema=WireTransfer,
    payload=WireTransfer(transfer_id=transfer_id, amount=50_000, to="acme.inc"),
    response_schema=Decision,
    timeout_seconds=900,
    # Tie retries to the business event. If your agent restarts, the
    # same transfer re-uses the same task instead of creating duplicates.
    idempotency_key=f"transfer:{transfer_id}",
)

verdict = "approved" if result.approved else "rejected"
print(f"✓ Transfer {verdict}. Reason: {result.reason}")`,
						typescript: `import { awaitHuman } from "awaithumans";
import { z } from "zod";

const WireTransfer = z.object({
  transferId: z.string(),
  amount: z.number(),
  to: z.string(),
});

const Decision = z.object({
  approved: z.boolean(),
  // short-answer field — the reviewer explains their call
  reason: z.string(),
});

async function main() {
  const transferId = "WT-2026-0042";

  console.log("→ creating task — go to the dashboard to review and complete it");

  const result = await awaitHuman({
    task: "Approve this wire transfer",
    payloadSchema: WireTransfer,
    payload: { transferId, amount: 50_000, to: "acme.inc" },
    responseSchema: Decision,
    timeoutMs: 900_000,
    // Tie retries to the business event. If your agent restarts, the
    // same transfer re-uses the same task instead of creating duplicates.
    idempotencyKey: \`transfer:\${transferId}\`,
  });

  const verdict = result.approved ? "approved" : "rejected";
  console.log(\`✓ Transfer \${verdict}. Reason: \${result.reason}\`);
}

main();`,
					}}
				/>
			) : (
				<div className="border border-white/10 rounded-lg overflow-hidden">
					<table className="w-full">
						<thead>
							<tr className="border-b border-white/10 text-left text-white/40 text-xs uppercase tracking-wider">
								<th className="px-4 py-3">Task</th>
								<th className="px-4 py-3">Status</th>
								<th className="px-4 py-3">Assigned To</th>
								<th className="px-4 py-3">Created</th>
								<th className="px-4 py-3">Timeout</th>
							</tr>
						</thead>
						<tbody>
							{tasks.map((task) => (
								<tr
									key={task.id}
									className="border-b border-white/5 hover:bg-white/5 transition-colors cursor-pointer"
									onClick={() => {
										router.push(`/task?id=${encodeURIComponent(task.id)}`);
									}}
								>
									<td className="px-4 py-3">
										<div className="font-medium text-sm">{task.task}</div>
										<div className="text-white/30 text-xs font-mono mt-0.5">
											{task.id.slice(0, TASK_ID_TRUNCATE_LENGTH)}...
										</div>
									</td>
									<td className="px-4 py-3">
										<StatusBadge status={task.status} />
									</td>
									<td className="px-4 py-3 text-sm text-white/60">
										{assigneeLabel(task) ?? (task.assign_to ? "Routed" : "—")}
									</td>
									<td className="px-4 py-3 text-sm text-white/40">
										{formatRelativeTime(task.created_at)}
									</td>
									<td className="px-4 py-3 text-sm text-white/40">
										{Math.round(task.timeout_seconds / SECONDS_PER_MINUTE)}m
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}

			{/* Pagination footer — rendered whenever we have rows OR we
			    are on a non-first page (so an over-shot offset can page
			    back). Hidden on the empty-state, which has its own
			    onboarding snippet. */}
			{(tasks.length > 0 || filters.offset > 0) && !loading && (
				<div className="mt-6 flex flex-wrap items-center gap-4">
					<label className="flex items-center gap-2 text-sm text-white/60">
						<span>Page size</span>
						<select
							value={filters.pageSize}
							onChange={(e) =>
								updateFilters({ pageSize: Number(e.target.value) })
							}
							className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-sm text-white focus:outline-none focus:border-brand/40"
						>
							{TASK_LIST_PAGE_SIZES.map((s) => (
								<option key={s} value={s}>
									{s}
								</option>
							))}
						</select>
					</label>
					<div className="ml-auto flex items-center gap-2 text-sm text-white/60">
						<span>Page {currentPage}</span>
						<button
							type="button"
							onClick={() =>
								updateFilters({
									offset: Math.max(0, filters.offset - filters.pageSize),
								})
							}
							disabled={filters.offset === 0}
							className="px-3 py-1.5 text-sm rounded-md border border-white/10 text-white/70 hover:bg-white/5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
						>
							← Prev
						</button>
						<button
							type="button"
							onClick={() =>
								updateFilters({
									offset: filters.offset + filters.pageSize,
								})
							}
							disabled={!hasNextPage}
							className="px-3 py-1.5 text-sm rounded-md border border-white/10 text-white/70 hover:bg-white/5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
						>
							Next →
						</button>
					</div>
				</div>
			)}
		</div>
	);
}
