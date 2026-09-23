import { applyDecodedLinks, attachThreads, issueNumber, renderEmail, renderWeb, resolveArticleIds, type RenderAssets, type RenderEnv, type Selections, type ThreadContext } from "../render/render.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { DECODED_LINKS, THREAD_CONTEXT } from "./index.js";

export const WEB_OUTPUT = "digest.html";
export const EMAIL_OUTPUT = "email.html";
// The render's clock and edition number, fixed by the first attempt so a retry renders the same bytes.
export const RENDER_CONTEXT = "render_context.json";
interface RenderContext { renderedAt: string; issueNo: number }

export interface RenderDeps { store: ArtifactStore; dbPath: string; assets: RenderAssets; env: RenderEnv; now?: () => Date }

// RENDER (newsroom/src/replay.py's tail, less the invariants): resolve article ids against the run's
// index, upgrade decoded Google-News links, attach thread context, then render the web issue and the
// email. threads and gnews are optional inputs: a pointer whose artifact the store does not hold
// means that stage has not run, and the render is the Python's without it.
export function renderActivity(deps: RenderDeps) {
  const { store } = deps;
  const optional = (p: Pointer, name: string): unknown => {
    if (p.name !== name) throw new Error(`render takes ${name}, not ${p.name}`);
    return store.find(p.runId, p.name) ? JSON.parse(store.get(p)) : undefined;
  };
  const write = (runId: number, name: string, text: string): Pointer => {
    const existing = store.find(runId, name);
    if (existing && store.get(existing) !== text) store.quarantine(runId, name);
    return store.put(runId, name, text);
  };
  return async (runId: number, selectionsPtr: Pointer, threads: Pointer, gnews: Pointer): Promise<{ html: Pointer; email: Pointer }> => {
    let selections = JSON.parse(store.get(selectionsPtr)) as Selections;
    const index = store.find(runId, "article_index.json");
    if (index) selections = resolveArticleIds(selections, JSON.parse(store.get(index)) as Record<string, unknown>);
    const links = optional(gnews, DECODED_LINKS) as Record<string, string> | undefined;
    if (links) selections = applyDecodedLinks(selections, links);
    const contexts = optional(threads, THREAD_CONTEXT) as Record<string, ThreadContext> | undefined;
    if (contexts) selections = attachThreads(selections, contexts);

    const ctxPtr = store.find(runId, RENDER_CONTEXT);
    let ctx: RenderContext;
    if (ctxPtr) ctx = JSON.parse(store.get(ctxPtr)) as RenderContext;
    else {
      const now = (deps.now ?? (() => new Date()))();
      const db = openDb(deps.dbPath);
      try {
        ctx = { renderedAt: now.toISOString(), issueNo: issueNumber(db, now.toISOString().slice(0, 10)) };
      } finally {
        db.close();
      }
      store.put(runId, RENDER_CONTEXT, JSON.stringify(ctx));
    }
    const input = { selections, now: new Date(ctx.renderedAt), issueNo: ctx.issueNo, env: deps.env, assets: deps.assets };
    const html = write(runId, WEB_OUTPUT, renderWeb(input));
    const email = write(runId, EMAIL_OUTPUT, renderEmail(input));
    console.log(JSON.stringify({ stage: "render", runId, mustKnow: selections.must_know.length, shouldKnow: selections.should_know.length, threads: contexts !== undefined, decodedLinks: links !== undefined }));
    return { html, email };
  };
}
