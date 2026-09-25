import MarkdownIt from "markdown-it";

// Markdown as a document: rendered under CommonMark with GFM tables, whitespace collapsed, and dropped
// where a block starts or ends (it renders as nothing there). A trailing space, `*` against `-` for a
// bullet, or a table's padding changes the bytes and nothing a reader or a model sees. Tests only:
// markdown-it is a development dependency.

const md = new MarkdownIt({ html: false, linkify: false });
const BLOCK = "(?:p|li|ul|ol|h[1-6]|table|thead|tbody|tr|th|td|blockquote|hr|pre)";
const blockSpace = new RegExp(`\\s*(</?${BLOCK}\\b[^>]*>)\\s*`, "g");

export const rendered = (text: string): string => md.render(text).replaceAll(/\s+/g, " ").replaceAll(blockSpace, "$1").trim();

export const sameDocument = (a: string, b: string): boolean => rendered(a) === rendered(b);
