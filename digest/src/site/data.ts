// What the site reads, as one port. The pages, the feed and the MCP tools depend on this interface;
// store.ts binds it to the product schema, and tests stand in for it with rows.

export interface IndexMeta {
  total: number;
  firstDate: string | null;
  newestDate: string | null;
  // Distinct (run, headline) across the two shown tiers.
  totalStories: number;
}

// One issue in the archive's running order, before the bias bar is computed from its sources.
export interface IssueRowData {
  date: string;
  preheader: string;
  // Oldest issue = 1.
  issueNo: number;
  must: number;
  should: number;
  // The newest issue of its calendar month: the row a "Month YYYY" divider precedes.
  isMonthStart: boolean;
  // Distinct source ids the issue's run shipped.
  sourceIds: string[];
}

export interface SearchHit {
  headline: string;
  tier: string;
  // The issue the headline was shown in; null when its run has no issue.
  date: string | null;
}

export interface ThreadSummaryData {
  id: number;
  label: string;
  status: string;
  // Timestamp text, "YYYY-MM-DD HH:MM:SS".
  updatedAt: string;
  updateCount: number;
  // The latest installment's stored content JSON.
  latestContent: string | null;
}

export interface ThreadIndexData {
  ongoing: ThreadSummaryData[];
  // One page of the rest, newest first, one row past the limit when more remain.
  older: ThreadSummaryData[];
  olderTotal: number;
}

export interface ThreadData {
  label: string;
  status: string;
  installments: { day: string; issueDate: string | null; story: string; content: string | null }[];
  // Open questions, newest raised first, with the content of the installment that raised each.
  openQuestions: { question: string; raisedContent: string | null }[];
}

export interface StatsData {
  sourceHealth: { sourceId: string; total: number; successes: number }[];
  sourceUsage: { sourceId: string; tier: string; count: number }[];
  recentRuns: { runAt: string; articlesKept: number; recipients: number; apiCostUsd: number | null }[];
  dedup: { count: number; avg: number | null; min: number | null; max: number | null };
  neverSelected: string[];
  cost: { runs: number; keptTotal: number; costTotal: number; shippedTotal: number; recipientsLatest: number };
}

export interface SiteData {
  indexMeta(): Promise<IndexMeta>;
  // Newest first. `year` scopes to one calendar year, whole; otherwise dates before `before`, at most
  // `limit` rows.
  archive(q: { before?: string | undefined; year?: number | undefined; limit: number }): Promise<IssueRowData[]>;
  issue(date: string): Promise<{ html: string; preheader: string } | undefined>;
  latestIssueDate(): Promise<string | undefined>;
  feed(limit: number): Promise<{ date: string; preheader: string }[]>;
  // A literal phrase, most relevant first, one row per story.
  search(query: string, limit: number): Promise<SearchHit[]>;
  threadIndex(before: { updatedAt: string; id: number } | undefined, limit: number): Promise<ThreadIndexData>;
  // undefined: no such thread. A number: the thread it was merged into.
  mergedInto(id: number): Promise<number | null | undefined>;
  thread(id: number): Promise<ThreadData | undefined>;
  // Every window is measured back from `now`, never from the database's clock.
  stats(days: number, now: Date): Promise<StatsData>;
  // Throws when the database cannot answer.
  ping(): Promise<void>;
}
