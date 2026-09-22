export type ArticleId = string & { readonly __brand: "ArticleId" };
const ID = /^A\d+$/;
export function parseArticleId(s: string): ArticleId {
  if (!ID.test(s)) throw new Error(`not an article id: ${JSON.stringify(s)}`);
  return s as ArticleId;
}
const URL_RE = /https?:\/\//i;
export function assertNoUrls(text: string): void {
  if (URL_RE.test(text)) throw new Error("a URL reached a model stage; the no-URL invariant is broken");
}
