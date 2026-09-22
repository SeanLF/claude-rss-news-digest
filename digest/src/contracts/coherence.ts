import { z } from "zod";
export const COHERENCE_FIELDS = ["headline", "summary", "why_it_matters"] as const;
export const FAILURE_KINDS = ["contradicted", "unsupported"] as const;
const Result = z.object({
  headline: z.string(),
  article_ids: z.array(z.string()),
  pass: z.boolean(),
  reason: z.string(),
  failed_fields: z.array(z.enum(COHERENCE_FIELDS)).optional(),
  failure_kinds: z.record(z.string(), z.enum(FAILURE_KINDS)).optional(),
});
export const CoherenceReportSchema = z.object({ results: z.array(Result) });
export type CoherenceReport = z.infer<typeof CoherenceReportSchema>;
export function coherenceReportJsonSchema(): Record<string, unknown> {
  // The Agent SDK validates against JSON Schema draft-07; zod targets 2020-12 by default.
  return z.toJSONSchema(CoherenceReportSchema, { target: "draft-07" });
}
