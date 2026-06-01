import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Vitest config for the dashboard.
 *
 * Two things this enables:
 *   1. JSX in `.tsx` files — the React Compiler plugin handles transformation
 *      so component test files can import directly from `index.tsx` etc.
 *      Pre-config, only pure-function `.ts` test files worked.
 *   2. happy-dom test environment so React Testing Library can mount
 *      components into a real document. Pure-function tests don't need
 *      this and are unaffected.
 *
 * The `@/...` path alias mirrors Next's tsconfig (paths: {"@/*": ["./*"]}).
 */
export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: {
			"@": resolve(__dirname, "./"),
		},
	},
	test: {
		environment: "happy-dom",
		globals: false,
	},
});
