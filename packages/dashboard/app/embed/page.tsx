"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import {
	FormRenderer,
	buildResponseValue,
	initialValueFor,
	type FormValue,
} from "@/components/form-renderer";
import { Markdown, looksRich } from "@/components/markdown";
import { EmbedFetchError, embedFetch } from "@/lib/embed/api";
import { postEmbed } from "@/lib/embed/post-message";
import { extractEmbedToken } from "@/lib/embed/token";
import type { CompleteTaskRequest, Task } from "@/lib/server";

export default function EmbedTaskPage() {
	return (
		<Suspense fallback={<div className="p-6 font-mono text-sm text-fg-2">Loading...</div>}>
			<EmbedTaskInner />
		</Suspense>
	);
}

function EmbedTaskInner() {
	const searchParams = useSearchParams();
	const taskId = searchParams.get("id") ?? "";
	const [token, setToken] = useState<string | null>(null);
	const [task, setTask] = useState<Task | null>(null);
	const [value, setValue] = useState<FormValue>({});
	const [submitting, setSubmitting] = useState(false);
	const [completed, setCompleted] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const parentOriginRef = useRef<string>("");
	// `submitting` is React state, which doesn't update synchronously —
	// a fast double-click fires `onSubmit` twice before the button's
	// `disabled` prop flips. The ref flips on the same tick so the
	// second call returns early.
	const inFlightRef = useRef(false);

	useEffect(() => {
		if (!taskId) {
			setError("Missing task id. The URL must include ?id=...");
			return;
		}
		const t = extractEmbedToken();
		if (!t) {
			setError("Missing embed token. The URL must include #token=...");
			postEmbed(parentOriginRef.current, {
				type: "task.error",
				payload: {
					taskId,
					code: "invalid_token",
					message: "Token missing from URL fragment",
				},
			});
			return;
		}
		setToken(t);
		try {
			const claims = JSON.parse(atob(t.split(".")[1])) as Record<string, unknown>;
			parentOriginRef.current =
				typeof claims.parent_origin === "string" ? claims.parent_origin : "";
		} catch {
			// best-effort decode; signature is verified server-side
		}
	}, [taskId]);

	useEffect(() => {
		if (!token || !taskId) return;
		void (async () => {
			try {
				const fetched = await embedFetch<Task>(`/api/tasks/${taskId}`, {
					token,
					method: "GET",
				});
				setTask(fetched);
				if (fetched.form_definition) {
					setValue(initialValueFor(fetched.form_definition));
				}
				// Reopening an embed URL after the task already completed
				// (e.g. partner stored the URL and the user clicks it
				// again) — show the done view, not the form.
				if (fetched.status === "completed") setCompleted(true);
				postEmbed(parentOriginRef.current, {
					type: "loaded",
					payload: { taskId },
				});
			} catch (e) {
				const code = e instanceof EmbedFetchError ? e.code : "internal";
				const message = e instanceof Error ? e.message : "Unknown error";
				setError(message);
				postEmbed(parentOriginRef.current, {
					type: "task.error",
					payload: { taskId, code, message },
				});
			}
		})();
	}, [token, taskId]);

	useEffect(() => {
		const report = () =>
			postEmbed(parentOriginRef.current, {
				type: "resize",
				payload: { height: document.documentElement.scrollHeight },
			});
		report();
		const ro = new ResizeObserver(report);
		ro.observe(document.documentElement);
		return () => ro.disconnect();
	}, [task, error]);

	const onSubmit = async () => {
		if (!token || !task) return;
		if (inFlightRef.current) return;
		inFlightRef.current = true;
		setSubmitting(true);
		setError(null);
		try {
			const cleaned = task.form_definition
				? buildResponseValue(task.form_definition, value)
				: value;
			const body: CompleteTaskRequest = {
				response: cleaned,
				completed_via_channel: "embed",
			};
			const result = await embedFetch<Task>(`/api/tasks/${taskId}/complete`, {
				token,
				method: "POST",
				body: JSON.stringify(body),
			});
			setTask(result);
			setCompleted(true);
			postEmbed(parentOriginRef.current, {
				type: "task.completed",
				payload: {
					taskId,
					response: result.response,
					completedAt: result.completed_at ?? new Date().toISOString(),
				},
			});
		} catch (e) {
			const code = e instanceof EmbedFetchError ? e.code : "internal";
			const message = e instanceof Error ? e.message : "Unknown error";
			setError(message);
			postEmbed(parentOriginRef.current, {
				type: "task.error",
				payload: { taskId, code, message },
			});
		} finally {
			setSubmitting(false);
			inFlightRef.current = false;
		}
	};

	if (error) {
		return (
			<div className="p-6 font-mono text-sm text-fg-2">
				<p className="mb-2 text-red-400">Error</p>
				<p>{error}</p>
			</div>
		);
	}

	if (!task) {
		return (
			<div className="p-6 font-mono text-sm text-fg-2">Loading task...</div>
		);
	}

	if (completed) {
		return (
			<div className="mx-auto max-w-xl p-6">
				{looksRich(task.task) ? (
					<div className="mb-4">
						<Markdown>{task.task}</Markdown>
					</div>
				) : (
					<h1 className="mb-4 font-mono text-lg font-medium">{task.task}</h1>
				)}
				<div className="rounded-md border border-brand/40 bg-brand/10 p-4 font-mono text-sm">
					<p className="mb-1 text-brand">Submitted</p>
					<p className="text-fg-2">Your response has been recorded.</p>
				</div>
			</div>
		);
	}

	return (
		<div className="mx-auto max-w-xl p-6">
			<h1 className="mb-4 font-mono text-lg font-medium">{task.task}</h1>
			{task.task_metadata && Object.keys(task.task_metadata).length > 0 ? (
				<div className="mb-4 rounded-md border border-brand/20 bg-brand/[0.03] p-3">
					<div className="space-y-2">
						{Object.entries(task.task_metadata).map(([key, value]) => (
							<div key={key} className="flex items-start gap-3">
								<span className="min-w-[100px] font-mono text-fg-2 text-xs">
									{key}
								</span>
								<span className="whitespace-pre-wrap break-words text-sm">
									{value}
								</span>
							</div>
						))}
					</div>
				</div>
			) : null}
			{task.form_definition ? (
				<>
					<FormRenderer
						form={task.form_definition}
						value={value}
						onChange={setValue}
						disabled={submitting}
					/>
					<button
						type="button"
						onClick={() => void onSubmit()}
						disabled={submitting}
						className="mt-6 rounded-md bg-brand px-4 py-2 font-mono text-sm font-medium text-black disabled:opacity-50"
					>
						{submitting ? "Submitting..." : "Submit"}
					</button>
				</>
			) : (
				<p className="font-mono text-sm text-fg-2">No form defined for this task.</p>
			)}
		</div>
	);
}
