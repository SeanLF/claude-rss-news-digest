// The TypeScript arms of the fulltext extractor fork (docs/2026-09-23-fulltext-extractor-fork.md),
// each returning plain text from a page's HTML, and production's truncation.
import { Readability } from "@mozilla/readability";
import { Defuddle } from "defuddle/node";
import { Readability as Smoothie } from "dom-smoothie-js";
import { parseHTML } from "linkedom";
import { z } from "zod";
import { scrubUrls } from "../contracts/ids.js";

export type Arm = "defuddle" | "readability" | "dom-smoothie";

const SmoothieArticle = z.object({ text_content: z.string().nullish() });

const SENTENCE_END = /[.!?](?=\s|$)/gu;
const MARKER = "\n[truncated]";

// fulltext.truncate_at_sentence: the cut is marked so a downstream reader never takes it for a whole
// article. Lengths are in code points, as Python's len counts them.
export function truncateAtSentence(text: string, maxChars: number): string {
  const cps = Array.from(text);
  if (cps.length <= maxChars) return text;
  let window = cps.slice(0, maxChars).join("");
  const ends = [...window.matchAll(SENTENCE_END)];
  const last = ends.at(-1);
  if (last) window = window.slice(0, last.index + 1);
  return window.trimEnd() + MARKER;
}

// Markdown link targets and images go; link text stays.
const stripMarkdownLinks = (md: string) => md.replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");

export async function extract(arm: Arm, html: string, url: string): Promise<string> {
  let text: string;
  if (arm === "defuddle") {
    const { document } = parseHTML(html);
    text = stripMarkdownLinks((await Defuddle(document, url, { markdown: true })).content ?? "");
  } else if (arm === "readability") {
    const { document } = parseHTML(html);
    text = new Readability(document).parse()?.textContent ?? "";
  } else {
    let parsed: unknown;
    try {
      parsed = new Smoothie(html, url, { text_mode: "Formatted" }).parse();
    } catch {
      parsed = undefined; // it throws when it finds no readable content
    }
    // Outside the catch: a changed result shape must fail loudly, not read as an empty page.
    text = parsed === undefined ? "" : (SmoothieArticle.parse(parsed).text_content ?? "");
  }
  return normalise(text);
}

// What every arm's text goes through before truncation, the reference included, so no arm is scored
// on a transform the others skip.
export const normalise = (text: string): string => scrubUrls(text.replace(/\n{3,}/g, "\n\n").trim());
