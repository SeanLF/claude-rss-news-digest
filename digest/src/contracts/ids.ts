export type ArticleId = string & { readonly __brand: "ArticleId" };
const ID = /^A\d+$/;
export function parseArticleId(s: string): ArticleId {
  if (!ID.test(s)) throw new Error(`not an article id: ${JSON.stringify(s)}`);
  return s as ArticleId;
}
// A scheme, or a protocol-relative `//host/…`: both are links a browser would follow.
// Hosts: a name with a letter TLD, or an IPv4 address.
const URL_RE = /(?:https?:)?\/\/(?:[a-z0-9.-]+\.[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})/i;
// The same host pattern, global, for replacing links with a token where the words matter and the
// address does not (archived summaries carry Hacker News "Article URL: https://…").
const URL_IN_TEXT = /(?:https?:)?\/\/(?:[a-z0-9.-]+\.[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})(?:[^\s"'<>)]*[^\s"'<>).,;:!?])?/gi; // never ends on punctuation
export function scrubUrls(text: string): string {
  return text.replace(URL_IN_TEXT, "[link]");
}
export function assertNoUrls(text: string): void {
  if (URL_RE.test(text)) throw new Error("a URL reached a model stage; the no-URL invariant is broken");
}
