// What an artifact's name encodes (data-model design, Appendix C): the stage that wrote it, what
// kind of record it is, and the branch of a fan-out it belongs to. Stored beside the name so a
// query can ask for "every WRITE output" without parsing names.
export interface ArtifactKind {
  stage: string | null;
  kind: string | null;
  branch: string | null;
}

const EXACT: Record<string, [stage: string, kind: string]> = {
  "sources.csv": ["prepare", "input"],
  "article_index.json": ["prepare", "input"],
  "recent_rss_titles.csv": ["prepare", "input"],
  "recent_digest_headlines.txt": ["prepare", "input"],
  "yesterday_headlines.txt": ["prepare", "input"],
  "cluster_tags.json": ["cluster", "output"],
  "clusters.json": ["cluster", "output"],
  "cluster_health.json": ["cluster", "health"],
  "cluster_cohesion.json": ["cluster", "health"],
  "recap.txt": ["recap", "output"],
  "weekly_recap.txt": ["recap", "output"],
  "selected.json": ["select", "output"],
  "write_branches.json": ["write", "health"],
  "draft_selections.json": ["assemble", "output"],
  "selections.json": ["assemble", "output"],
  "preheader.json": ["preheader", "output"],
  "coherence_report.json": ["coherence", "output"],
  "thinking_coherence.txt": ["coherence", "thinking"],
  "repair_requests.json": ["repair", "output"],
  "repair_resolution.json": ["repair", "output"],
  "repair_health.json": ["repair", "health"],
  "thinking_repair_recheck.txt": ["repair", "thinking"],
  "thread_links.json": ["threads", "output"],
  "thread_assignments.json": ["threads", "output"],
  "thread_installments.json": ["threads", "output"],
  "thread_context.json": ["threads", "output"],
  "thread_health.json": ["threads", "health"],
  "article_fulltext.json": ["fulltext", "output"],
  "fulltext_health.json": ["fulltext", "health"],
  "gnews_links.json": ["gnews", "output"],
  "gnews_health.json": ["gnews", "health"],
  "digest.html": ["render", "output"],
  "email.html": ["render", "output"],
  "render_context.json": ["render", "output"],
  "models.json": ["run", "trace"],
  "force_undo_threads.json": ["run", "trace"],
  "temporal_history.json": ["run", "trace"],
};

const PATTERNS: [RegExp, stage: string, kind: string, branch: (m: RegExpMatchArray) => string][] = [
  [/^articles_(\d+)\.csv$/, "prepare", "input", (m) => `c${m[1]}`],
  [/^cluster_tags_b(\d+)\.json$/, "cluster-extract", "output", (m) => `b${m[1]}`],
  [/^draft_s(\d+)\.json$/, "write", "output", (m) => `s${m[1]}`],
  [/^thinking_write_s(\d+)\.txt$/, "write", "thinking", (m) => `s${m[1]}`],
  [/^thread_synthesis_t(\d+)\.json$/, "threads", "output", (m) => `t${m[1]}`],
  [/^thread_audit_t(\d+)\.json$/, "threads", "output", (m) => `t${m[1]}`],
];

export function artifactKind(name: string): ArtifactKind {
  const exact = EXACT[name];
  if (exact) return { stage: exact[0], kind: exact[1], branch: null };
  for (const [re, stage, kind, branch] of PATTERNS) {
    const m = name.match(re);
    if (m) return { stage, kind, branch: branch(m) };
  }
  return { stage: null, kind: null, branch: null };
}
