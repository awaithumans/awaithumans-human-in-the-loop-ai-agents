"use client";

/**
 * Compact context — when true, the FieldWrapper suppresses the per-field
 * label and the required-marker asterisk. Used by the RepeatableGroup
 * renderer to render each row's cells without duplicating the column
 * headers (which already carry the label).
 *
 * Kept as a context (not a prop) so we don't have to thread a `compact`
 * boolean through every primitive renderer's props. The cost is one
 * React Context read per FieldWrapper render — negligible.
 *
 * Hints stay visible even in compact mode: they often carry the human
 * pattern hint ("Numbers only") that the column header can't fit.
 */

import { createContext } from "react";

export const CompactFieldContext = createContext<boolean>(false);
