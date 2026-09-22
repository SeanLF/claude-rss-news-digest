import { z } from "zod";

// One article's extracted tags. Every value is a string: model JSON is not normalised
// (`"entities": null`, `"entities": [2026]`) and the bag builder lowercases directly.
export interface Tag {
  entities: string[];
  keywords: string[];
  primary_event: string;
}

export const TAG_BAG_WEIGHTS = { entities: 3, keywords: 1, primary_event: 2 } as const;

// sklearn's default token pattern `(?u)\b\w\w+\b`: a run of two or more word characters.
export const TOKEN_RE = /[\p{L}\p{N}_]{2,}/u;

// Model JSON is tolerated where the Python coerces it: null lists, numbers among strings.
export const ExtractItemSchema = z.object({
  article_id: z.string(),
  entities: z.array(z.union([z.string(), z.number()])).nullish(),
  keywords: z.array(z.union([z.string(), z.number()])).nullish(),
  primary_event: z.union([z.string(), z.number()]).nullish(),
});
export const ExtractItemsSchema = z.object({ items: z.array(ExtractItemSchema) });

// Shape only, never count (2026-08-21): the model is decoded against this.
export function extractItemsJsonSchema(): Record<string, unknown> {
  return z.toJSONSchema(ExtractItemsSchema, { target: "draft-07" });
}

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.flatMap((x: unknown) => (typeof x === "string" ? [x] : typeof x === "number" ? [String(x)] : [])) : []);

export function coerceTag(item: object): Tag {
  const o = item as Record<string, unknown>;
  const pe = o["primary_event"];
  return { entities: strs(o["entities"]), keywords: strs(o["keywords"]), primary_event: typeof pe === "string" ? pe : typeof pe === "number" ? String(pe) : "" };
}

// Term bag: entities x3 (the strongest same-story signal), keywords x1, primary_event x2.
// "" when the article has no usable tags; the caller substitutes a per-article unique token so
// tagless articles stay singletons instead of collapsing into one junk blob.
export function tagBag(tag: Tag | undefined): string {
  if (!tag) return "";
  const ents = tag.entities.map((e) => e.toLowerCase());
  const kws = tag.keywords.map((k) => k.toLowerCase());
  const pe = tag.primary_event.toLowerCase().trim();
  const parts = [
    ...Array<string[]>(TAG_BAG_WEIGHTS.entities).fill(ents).flat(),
    ...Array<string[]>(TAG_BAG_WEIGHTS.keywords).fill(kws).flat(),
    ...(pe ? Array<string>(TAG_BAG_WEIGHTS.primary_event).fill(pe) : []),
  ];
  return parts.filter((p) => p.trim()).join(" ");
}

export const usable = (tag: Tag | undefined): boolean => TOKEN_RE.test(tagBag(tag));

// The items a batch's response may key: article_id must be one THIS batch asked about, first
// seen wins. A model that renumbers its output would otherwise write one batch's tags onto
// another batch's articles, silently.
export function itemsForBatch<T extends { article_id: string }>(items: T[], batch: string[]): T[] {
  const wanted = new Set(batch);
  const seen = new Set<string>();
  const out: T[] = [];
  for (const it of items) {
    if (wanted.has(it.article_id) && !seen.has(it.article_id)) {
      seen.add(it.article_id);
      out.push(it);
    }
  }
  return out;
}
