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
import { completedByLabel, formatRelativeTime } from "@/lib/utils";
import {
	TASK_ID_TRUNCATE_LENGTH,
	TASK_LIST_DEFAULT_PAGE_SIZE,
	TASK_LIST_PAGE_SIZES,
	TASK_LIST_POLL_INTERVAL_MS,
} from "@/lib/constants";
import {
	TaskFilterBar,
	type StatusOption,
} from "@/components/filters/task-filter-bar";
import { CopyButton } from "@/components/copy-button";
import { ShellEmptyState } from "@/components/shell-empty-state";
import { StatusBadge } from "@/components/status-badge";
import { TerminalSpinner } from "@/components/terminal-spinner";

/**
 * Audit log = the tasks page restricted to terminal statuses, with
 * a filter bar tailored to "what already happened" rather than
 * "what's still open." Server enforces the terminal-only scope via
 * `?terminal=true` (PR #73), so we don't have to filter client-side
 * and pagination works correctly.
 */

const STATUS_OPTIONS: readonly StatusOption[] = [
	{ label: "All terminal", value: "all" },
	{ label: "Completed", value: "completed" },
	{ label: "Timed out", value: "timed_out" },
	{ label: "Cancelled", value: "cancelled" },
	{ label: "Verification exhausted", value: "verification_exhausted" },
] as const;

interface FilterState {
	status: TaskStatus | "all";
	assignedTo: string;
	unassigned: boolean; // unused on this page (audit = post-assignment) but
	mine: boolean; //         kept in shape so we can share <TaskFilterBar>.
	pageSize: number;
	offset: number;
}

function readFiltersFromSearchParams(params: URLSearchParams): FilterState {
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
		unassigned: false,
		mine: params.get("mine") === "true",
		pageSize,
		offset,
	};
}

function filtersToSearchParams(state: FilterState): URLSearchParams {
	const sp = new URLSearchParams();
	if (state.status !== "all") sp.set("status", state.status);
	if (state.assignedTo) sp.set("assignedTo", state.assignedTo);
	if (state.mine) sp.set("mine", "true");
	if (state.pageSize !== TASK_LIST_DEFAULT_PAGE_SIZE)
		sp.set("pageSize", String(state.pageSize));
	if (state.offset > 0) sp.set("offset", String(state.offset));
	return sp;
}

export default function AuditLogPage() {
	return (
		<Suspense fallback={<TerminalSpinner label="awaiting audit entries" size="md" />}>
			<AuditLogPageInner />
		</Suspense>
	);
}

function AuditLogPageInner() {
	const router = useRouter();
	const searchParams = useSearchParams();
	const filters = useMemo(
		() => readFiltersFromSearchParams(searchParams),
		[searchParams],
	);

	const [tasks, setTasks] = useState<Task[]>([]);
	const [loading, setLoading] = useState(true);
	const [me, setMe] = useState<MeResponse | null>(null);

	const updateFilters = useCallback(
		(patch: Partial<FilterState>) => {
			const offsetBumped = "offset" in patch && patch.offset !== undefined;
			const next: FilterState = {
				...filters,
				...patch,
				offset: offsetBumped ? (patch.offset as number) : 0,
			};
			const sp = filtersToSearchParams(next);
			const query = sp.toString();
			router.replace(query ? `/audit?${query}` : "/audit", {
				scroll: false,
			});
		},
		[filters, router],
	);

	const loadTasks = useCallback(async () => {
		try {
			const params: Parameters<typeof fetchTasks>[0] = {
				terminal: true,
				limit: filters.pageSize,
				offset: filters.offset,
			};
			if (filters.status !== "all") params.status = filters.status;
			if (filters.mine && me?.email) params.assigned_to = me.email;
			else if (filters.assignedTo) params.assigned_to = filters.assignedTo;
			const data = await fetchTasks(params);
			setTasks(data);
		} finally {
			setLoading(false);
		}
	}, [filters, me?.email]);

	useEffect(() => {
		fetchMe().then(setMe).catch(() => setMe(null));
	}, []);

	useEffect(() => {
		loadTasks();
		const interval = setInterval(loadTasks, TASK_LIST_POLL_INTERVAL_MS);
		return () => clearInterval(interval);
	}, [loadTasks]);

	const currentPage = Math.floor(filters.offset / filters.pageSize) + 1;
	const hasNextPage = tasks.length === filters.pageSize;

	const filtersActive =
		filters.status !== "all" || filters.assignedTo !== "" || filters.mine;

	return (
		<div>
			<div className="mb-6">
				<h1 className="text-2xl font-bold">Audit Log</h1>
				<p className="text-white/40 text-sm mt-1">
					Who approved what, when, and how.
				</p>
			</div>

			<TaskFilterBar
				filters={{
					status: filters.status,
					assignedTo: filters.assignedTo,
					unassigned: false,
					mine: filters.mine,
				}}
				onChange={updateFilters}
				isOperator={!!me?.is_operator}
				statusOptions={STATUS_OPTIONS}
				showUnassignedToggle={false}
				searchPlaceholder="search assignee — email, name, or Slack ID"
			/>

			{loading ? (
				<TerminalSpinner label="awaiting audit entries" size="md" />
			) : tasks.length === 0 ? (
				filtersActive ? (
					<div className="border border-white/10 rounded-lg p-8 text-center text-white/50 text-sm">
						No terminal tasks match these filters.
					</div>
				) : (
					<ShellEmptyState
						heading="tail -f audit.log — no terminal events yet"
						note="Completed, timed-out, and cancelled tasks land here the moment they close."
					/>
				)
			) : (
				<div className="border border-white/10 rounded-lg overflow-hidden">
					<table className="w-full">
						<thead>
							<tr className="border-b border-white/10 text-left text-white/40 text-xs uppercase tracking-wider">
								<th className="px-4 py-3">Task</th>
								<th className="px-4 py-3">Status</th>
								<th className="px-4 py-3">Completed By</th>
								<th className="px-4 py-3">Channel</th>
								<th className="px-4 py-3">Completed At</th>
								<th className="px-4 py-3">Duration</th>
							</tr>
						</thead>
						<tbody>
							{tasks.map((task) => {
								const createdAt = new Date(task.created_at).getTime();
								const endedAt = task.completed_at
									? new Date(task.completed_at).getTime()
									: task.timed_out_at
										? new Date(task.timed_out_at).getTime()
										: Date.now();
								const durationMs = endedAt - createdAt;
								const durationMin = Math.round(durationMs / 60000);

								return (
									<tr
										key={task.id}
										className="border-b border-white/5 hover:bg-white/5 transition-colors cursor-pointer"
										onClick={() => {
											router.push(
												`/task?id=${encodeURIComponent(task.id)}`,
											);
										}}
									>
										<td className="px-4 py-3">
											<div className="text-sm font-medium">{task.task}</div>
											<div className="flex items-center gap-1 mt-0.5">
												<span className="text-white/30 text-xs font-mono">
													{task.id.slice(0, TASK_ID_TRUNCATE_LENGTH)}...
												</span>
												<CopyButton code={task.id} />
											</div>
										</td>
										<td className="px-4 py-3">
											<StatusBadge status={task.status} />
										</td>
										<td className="px-4 py-3 text-sm text-white/60">
											{completedByLabel(task) ?? "—"}
										</td>
										<td className="px-4 py-3 text-sm text-white/40">
											{task.completed_via_channel ?? "—"}
										</td>
										<td className="px-4 py-3 text-sm text-white/40">
											{task.completed_at
												? formatRelativeTime(task.completed_at)
												: task.timed_out_at
													? formatRelativeTime(task.timed_out_at)
													: "—"}
										</td>
										<td className="px-4 py-3 text-sm text-white/40">
											{durationMin < 1 ? "<1m" : `${durationMin}m`}
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			)}

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
