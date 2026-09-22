// Mirrors newsroom/src/schema.py: SOURCE_SCHEMA, REPORTING_VARIES_SCHEMA, ARTICLE_SCHEMA,
// SHOULD_KNOW_ARTICLE_SCHEMA, SELECTIONS_SCHEMA. `.strict()` is additionalProperties: false.
import { z } from "zod";
export const PREHEADER_MAX_CHARS = 157;
export const NOT_COVERED_BLURB_MAX_LEN = 500;
const Source = z.object({ article_id: z.string().regex(/^A\d+$/) }).strict();
const ReportingVaries = z.object({ source: z.string(), angle: z.string(), bias: z.string() }).strict();
const Story = z
  .object({
    headline: z.string(),
    summary: z.string(),
    why_it_matters: z.string(),
    sources: z.array(Source).min(1),
    reporting_varies: z.array(ReportingVaries).optional(),
    cluster_id: z.string().optional(),
  })
  .strict();
// Briefs render headline + summary only; why_it_matters is tolerated, not required (archived
// selections before 2026-09-03 carry one, and --resume re-renders those).
const Brief = Story.extend({ why_it_matters: z.string().optional() }).strict();
export const SelectionsSchema = z
  .object({
    must_know: z.array(Story),
    should_know: z.array(Brief),
    preheader: z.string().max(PREHEADER_MAX_CHARS),
    not_covered_blurb: z.string().max(NOT_COVERED_BLURB_MAX_LEN).optional(),
  })
  .strict();
export type Selections = z.infer<typeof SelectionsSchema>;
export type Story = z.infer<typeof Story>;
