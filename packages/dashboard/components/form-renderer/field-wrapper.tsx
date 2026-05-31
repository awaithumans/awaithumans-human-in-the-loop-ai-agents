import { useContext, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { FormField } from "@/lib/form-types";
import { CompactFieldContext } from "./compact-context";

type Props = {
	field: FormField;
	children: ReactNode;
	className?: string;
};

/**
 * Shared wrapper: renders label (with required marker), the field content,
 * and an optional hint line. Layout-only primitives render without a wrapper
 * so they shouldn't use this.
 *
 * Compact mode (via CompactFieldContext) suppresses the label and required
 * marker — used by the RepeatableGroup renderer where the column header
 * already carries that information. Hints stay visible: they often carry
 * the human pattern hint ("Numbers only") that the header can't fit.
 */
export function FieldWrapper({ field, children, className }: Props) {
	const compact = useContext(CompactFieldContext);
	const hasLabel = !compact && field.label && field.label.length > 0;
	return (
		<div className={cn("space-y-1.5", className)}>
			{hasLabel && (
				<label htmlFor={field.name} className="block text-sm text-white/70">
					{field.label}
					{field.required && (
						<span className="text-red-400 ml-0.5" aria-hidden>
							*
						</span>
					)}
				</label>
			)}
			{children}
			{field.hint && <p className="text-white/40 text-xs">{field.hint}</p>}
		</div>
	);
}
