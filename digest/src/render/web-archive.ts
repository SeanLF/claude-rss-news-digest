import { defaultTreeAdapter as t, parse, serialize, type DefaultTreeAdapterMap } from "parse5";

type ParentNode = DefaultTreeAdapterMap["parentNode"];
const STRIPPED = new Set(["email-only", "preheader"]);

function strip(parent: ParentNode): void {
  for (const node of t.getChildNodes(parent).slice()) { // detaching mutates the live list
    if (t.isCommentNode(node)) {
      if (t.getCommentNodeContent(node).includes("[if mso")) t.detachNode(node);
    } else if (t.isElementNode(node)) {
      const cls = t.getAttrList(node).find((a) => a.name === "class")?.value.split(/\s+/) ?? [];
      if (cls.some((c) => STRIPPED.has(c))) t.detachNode(node);
      else strip("content" in node ? node.content : node);
    }
  }
}

// db.prepare_for_web: the web archive's copy of the issue, stripped of what only the inbox shows
// (email-only nodes, the preheader, the Outlook-only MSO conditionals). The web tier injects its
// chrome at fixed string needles in this blob, so it is re-serialised by the spec parser, which
// keeps each element's attributes in their written order.
export function webArchiveHtml(html: string): string {
  const doc = parse(html);
  strip(doc);
  return serialize(doc);
}
