"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { LogoMark } from "@/components/logo";
import { TerminalSpinner } from "@/components/terminal-spinner";
import {
	UnauthorizedError,
	fetchMe,
	fetchSetupStatus,
	login,
} from "@/lib/server";

/**
 * Wrapping the search-params reader in Suspense is required for
 * Next's static export (output: "export"). Without it, the build
 * fails with "useSearchParams() should be wrapped in a suspense
 * boundary at page /login".
 */
export default function LoginPage() {
	return (
		<Suspense
			fallback={
				<div className="min-h-screen flex items-center justify-center">
					<TerminalSpinner label="checking session" />
				</div>
			}
		>
			<LoginPageInner />
		</Suspense>
	);
}

function LoginPageInner() {
	const router = useRouter();
	const params = useSearchParams();
	const next = params.get("next") || "/";

	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [ready, setReady] = useState(false);

	// On mount: if the server has no users yet, bounce to /setup.
	// If the caller is already logged in, bounce to `next`. Otherwise
	// render the login form.
	useEffect(() => {
		(async () => {
			try {
				const setup = await fetchSetupStatus();
				if (setup.needs_setup) {
					router.replace("/setup");
					return;
				}
				const me = await fetchMe();
				if (me.authenticated) {
					router.replace(next);
					return;
				}
			} catch {
				// Server unreachable — fall through and let the user try.
			}
			setReady(true);
		})();
	}, [router, next]);

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setSubmitting(true);
		setError(null);
		try {
			await login(email, password);
			router.replace(next);
		} catch (err) {
			setError(describeLoginError(err));
		} finally {
			setSubmitting(false);
		}
	};

	if (!ready) {
		return (
			<div className="min-h-screen flex items-center justify-center">
				<TerminalSpinner label="checking session" />
			</div>
		);
	}

	return (
		<div className="min-h-screen flex items-center justify-center px-6">
			<div className="w-full max-w-sm">
				<div className="flex items-center gap-2.5 mb-8 justify-center">
					<LogoMark size={24} className="text-fg" />
					<span className="font-mono font-semibold tracking-tight text-base">
						awaithumans
					</span>
				</div>

				<div className="border border-white/10 rounded-lg p-6 bg-white/[0.02]">
					<h1 className="text-lg font-semibold mb-1">Sign in</h1>
					<p className="text-white/40 text-xs mb-5">
						Use the operator credentials you created during first-run setup.
					</p>

					<form onSubmit={handleSubmit} className="space-y-4">
						<Field
							label="Email"
							placeholder="you@yourcompany.com"
							type="email"
							value={email}
							onChange={setEmail}
							autoFocus={!email}
							disabled={submitting}
						/>
						<Field
							label="Password"
							placeholder="your operator password"
							type="password"
							value={password}
							onChange={setPassword}
							autoFocus={!!email}
							disabled={submitting}
						/>

						{error && (
							<div className="text-red-400 text-xs border border-red-400/30 bg-red-400/5 rounded-md px-3 py-2">
								{error}
							</div>
						)}

						<button
							type="submit"
							disabled={submitting || !password || !email}
							className="w-full px-4 py-2.5 bg-brand text-black font-semibold text-sm rounded-md hover:bg-brand/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
						>
							{submitting ? "Signing in…" : "Sign in"}
						</button>
					</form>

					<div className="mt-5 pt-4 border-t border-white/5">
						<p className="text-white/35 text-[11px] leading-relaxed">
							<span className="text-white/50">Forgot your password?</span>{" "}
							Reset it from the terminal:
						</p>
						<pre className="mt-2 text-[11px] font-mono text-white/60 bg-black/30 border border-white/10 rounded px-2.5 py-1.5 overflow-x-auto">
							<code>awaithumans set-password your@email.com</code>
						</pre>
					</div>
				</div>

				<p className="text-center text-white/25 text-xs mt-6">
					First time?{" "}
					<a href="/setup" className="text-brand/80 hover:text-brand">
						Set up the first operator
					</a>
					.
				</p>
			</div>
		</div>
	);
}

function Field({
	label,
	placeholder,
	value,
	onChange,
	type = "text",
	autoFocus,
	disabled,
}: {
	label: string;
	placeholder?: string;
	value: string;
	onChange: (v: string) => void;
	type?: string;
	autoFocus?: boolean;
	disabled?: boolean;
}) {
	return (
		<label className="block">
			<span className="text-xs font-medium text-white/60 mb-1.5 block">
				{label}
			</span>
			<input
				type={type}
				value={value}
				onChange={(e) => onChange(e.target.value)}
				placeholder={placeholder}
				autoFocus={autoFocus}
				disabled={disabled}
				className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm placeholder:text-white/20 focus:outline-none focus:border-brand/40 disabled:opacity-40"
			/>
		</label>
	);
}

/**
 * Turn a login-endpoint error into a user-facing message. Four cases,
 * in priority order:
 *   - UnauthorizedError (401 from apiFetch): wrong credentials
 *   - TypeError (fetch network failure): server unreachable
 *   - Anything else with a message (ApiError for 5xx, generic Error):
 *     show the server's own message verbatim — it's already written
 *     for humans by the server's ServiceError handler
 *   - Anything without a message: generic fallback
 */
function describeLoginError(err: unknown): string {
	if (err instanceof UnauthorizedError) {
		return "Invalid credentials. Check your email and password.";
	}
	if (err instanceof TypeError) {
		return "Can't reach the server. Is `awaithumans dev` still running?";
	}
	if (err instanceof Error) {
		return err.message;
	}
	return "Sign-in failed. Please try again.";
}
