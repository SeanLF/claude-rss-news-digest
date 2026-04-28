# Content Vertical Assessment for AI Digest Pipeline

**Research date:** April 2026
**Scope:** 11 verticals assessed for fit with the existing RSS-to-Claude-to-email pipeline

---

## Summary Table

| Vertical | Volume/day | Frequency | Audience WTP | Existing tools | Fit score |
|---|---|---|---|---|---|
| ML/AI Research Papers | 200-300 (cs.LG+cs.AI) | Daily or 3x/week | $20-50/mo individual; $200+/mo team | ResearchRabbit, Semantic Scholar, TLDR AI | 4/5 |
| Biology / Life Sciences Research | 500-1000+ (PubMed broad) | Weekly per sub-topic | $30-80/mo professional | Semantic Scholar, ReadCube, journal alerts | 3/5 |
| Climate Science Research | 30-80 (targeted) | Weekly | $10-30/mo individual; grant-funded orgs | Carbon Brief (free), Inside Climate News | 3/5 |
| Economics Research | 40-100 (NBER + SSRN + journals) | Weekly | $0 (academic norm); $50+/mo policy shops | NBER Digest (free), VoxEU | 2/5 |
| Niche Sports (Hockey / F1) | 50-150 | Daily in season | $5-15/mo individual | The Athletic ($12-18/mo), Substack verticals | 2/5 |
| Esports | 100-300 | Daily | $5-10/mo individual | Dot Esports, ESPN Esports, Upcomer | 2/5 |
| Mining / Resources Industry | 30-80 | Daily or 3x/week | $100-500/mo B2B | Mining.com, International Mining, Mining Journal | 4/5 |
| Oil, Gas & Energy | 100-300 | Daily | $200-2000/mo B2B | S&P Global Platts, OilPrice.com, Energy Monitor | 3/5 |
| Sustainability / ESG Consulting | 50-150 | Weekly or 3x/week | $100-300/mo B2B | ESG Today, Responsible Investor, GreenBiz | 4/5 |
| Policy & Regulation (EU / US Federal) | 100-300 | Daily | $5,000-10,000+/yr enterprise | Politico Pro (~$10k/yr), Bloomberg Government | 4/5 |
| Open Source / Developer Tools | 200-500 (HN + GitHub + blogs) | Daily | $0-10/mo (ad-supported market) | TLDR ($7M revenue, sponsor-only), Changelog, Console | 2/5 |
| Investment Research / VC News | 100-400 | Daily | $50-200/mo professional; $999/yr+ institutional | The Information, Axios Pro, PitchBook | 3/5 |

---

## Vertical Analysis

### 1. ML/AI Research Papers

**Sources:** arXiv RSS (cs.LG, cs.AI, cs.CL, cs.CV -- category-level feeds update daily), Papers With Code, Hugging Face papers, Semantic Scholar API, Twitter/X paper threads. All freely available with stable RSS.

**Volume:** October 2024 saw cs.LG + cs.CV + cs.CL alone exceed 6,000 submissions monthly -- roughly 200-300 papers/day across the core ML categories. Total arXiv hit a monthly record of 24,226 in October 2024.

**Frequency:** Daily makes sense; the field moves fast and readers are conditioned to daily updates (TLDR AI is daily).

**Audience & WTP:** ML engineers, researchers, and product teams at AI companies. Willingness to pay is genuine but suppressed by free alternatives. Individual WTP: $20-50/mo. Team/enterprise: $200-500/mo if well-curated with business context (what does this mean for our stack?). Strong sponsorship revenue available given the advertiser profile.

**Curation complexity:** High. The pipeline must assess novelty (is this incremental or a step change?), methodological soundness (from abstract alone), and relevance to the reader's domain. Claude is well-positioned for this vs. keyword filters. Cross-paper synthesis -- "three papers this week converge on the same finding" -- is where AI adds real value.

**Existing tools:** ResearchRabbit, Semantic Scholar, Elicit, Papers With Code alerts are discovery tools, not digests. TLDR AI newsletter exists and is free/sponsored. The gap is curated editorial voice + business context, not raw discovery.

**Fit score: 4/5.** The pipeline maps well. Volume is manageable with category scoping. Main risk: the free tier of competitors is strong and individual WTP is soft. The play is team/enterprise with a "so what for practitioners" angle.

---

### 2. Biology / Life Sciences Research

**Sources:** PubMed RSS (40M+ citations, arbitrary search queries supported), bioRxiv, medRxiv, Nature/Science/Cell RSS (paywalled articles but open abstracts). PubMed supports up to 1000 items per custom RSS feed.

**Volume:** PubMed adds roughly 4,000-5,000 papers/day across all biomedical topics. Within a sub-topic (e.g., CRISPR, cancer immunotherapy), volume drops to 20-100/day -- manageable. Broad "biology" is unworkable without scoping.

**Frequency:** Weekly per sub-topic. Daily would be overwhelming and the research cadence doesn't demand it.

**Audience & WTP:** Academic researchers (low WTP, resource-constrained), biotech/pharma professionals (high WTP, $50-200/mo), science journalists (moderate WTP). The viable segment is industry professionals who need to track competitive research. Academic segment is nearly impossible to monetize directly.

**Curation complexity:** Very high. Assessing methodology quality from an abstract requires domain expertise the prompt would need to encode carefully. Different sub-fields have incompatible quality signals. Risk of confident-sounding but wrong curation.

**Existing tools:** Semantic Scholar, ReadCube Papers, Feedly Pro with journal feeds, journal-specific email alerts (free). No strong opinionated editorial digest equivalent to what this pipeline produces.

**Fit score: 3/5.** Technically feasible if scoped to a sub-domain with a well-defined professional audience (e.g., oncology drug development, gene therapy). Too broad = noise. The methodology-quality curation challenge is the main risk -- Claude summarising abstracts without full-text access will miss important caveats.

---

### 3. Climate Science Research

**Sources:** Carbon Brief RSS (free, high quality), arXiv (physics.ao-ph, eess.SP climate-adjacent), IPCC reports, Nature Climate Change, Geophysical Research Letters, NASA/NOAA press releases. Good RSS coverage.

**Volume:** Narrow topic: 30-80 relevant items/day across all sources. Well within pipeline range.

**Frequency:** Weekly. The research cadence and reader expectation align with weekly synthesis.

**Audience & WTP:** Climate scientists (low WTP), NGO/policy staff (low-moderate WTP, often grant-funded), energy transition consultants (moderate WTP $30-80/mo), corporate sustainability teams (moderate-high WTP $100-300/mo team). The corporate sustainability angle overlaps with the ESG vertical (see below) and may be better addressed there.

**Curation complexity:** Moderate. The field has clear signals for significance (journal tier, policy implications, departure from consensus). Carbon Brief already does expert lay-person translation well. The pipeline would need to avoid reproducing what Carbon Brief does for free.

**Existing tools:** Carbon Brief's Daily Briefing and DeBriefed newsletters are high-quality and free. Inside Climate News, DeSmog. These are journalism-focused; a research-paper-focused digest is less covered.

**Fit score: 3/5.** Viable as a research-paper-focused supplement to Carbon Brief, but the free existing tools are strong. Best as part of a broader ESG/sustainability vertical rather than standalone.

---

### 4. Economics Research

**Sources:** NBER Working Papers (free digest and RSS), SSRN, VoxEU, IMF Working Papers, World Bank working papers, Fed Reserve research. Rich RSS coverage.

**Volume:** NBER publishes roughly 20-30 working papers per week. Adding SSRN economics and international institutions: 40-100 items/day across the whole field.

**Frequency:** Weekly. Academic economics moves deliberately; a weekly digest is the established norm (NBER Digest is monthly, VoxEU is near-daily but accessible).

**Audience & WTP:** Academic economists (will not pay), policy analysts (institutional budget, won't pay personally), financial market economists at banks/funds (high WTP if market-relevant), journalists (low WTP). WTP is structurally weak because the NBER Digest is free and authoritative.

**Curation complexity:** High. Economic papers require understanding methodology, empirical strategy, and whether findings are robust. "Working paper" ≠ peer-reviewed. The pipeline would summarise pre-publication work, which carries reputational risk if a high-profile paper is later discredited.

**Existing tools:** NBER Digest (free, authoritative), VoxEU (free, expert-written), Peterson Institute, Brookings. Well-served free tier.

**Fit score: 2/5.** The free alternatives are too strong and the paying segment (financial economists) is already served by Bloomberg and Refinitiv. Structurally weak WTP outside institutional settings.

---

### 5. Niche Sports: Hockey / F1

**Sources:** Team RSS feeds, beat reporter Substacks, league official RSS (NHL.com, Formula1.com), Sportsnet, TSN, ESPN. Generally good RSS availability but quality varies widely.

**Volume:** 50-150 items/day per sport during season; much lower in off-season.

**Frequency:** Daily during season. The audience expects near-real-time updates, not next-morning synthesis.

**Audience & WTP:** Passionate fans, $5-15/mo. The Athletic demonstrated the market at $12-18/mo. Team-specific newsletters (e.g., a single NHL franchise's dedicated newsletter) can command $5-10/mo from a small but loyal base.

**Curation complexity:** Low-to-moderate. Editorial judgment is about story significance and team/player framing, not technical assessment. AI curation of sports beats is mostly done well already by aggregators.

**Existing tools:** The Athletic reached 5M newsletter subscribers by May 2025 and covers hockey and F1 explicitly. The Athletic's Red Light (hockey) and F1 coverage newsletters exist. Substack has dozens of team-specific newsletters. This space is mature.

**Fit score: 2/5.** The category works economically (The Athletic proves it), but it is crowded with well-funded players who have proprietary journalism relationships. The pipeline has no editorial advantage over sourcing existing quality journalism. AI curation of sports beats doesn't add enough value over a human-curated feed.

---

### 6. Esports

**Sources:** Dot Esports RSS, ESPN Esports, Upcomer, Liquipedia (event data, no RSS), game-specific subreddits, Twitch/YouTube event streams. Coverage exists but is fragmented and platform-native.

**Volume:** 100-300 items/day across all titles. Per-game (e.g., LoL, Valorant, CS2): 20-60/day.

**Frequency:** Daily during active events, 3x/week otherwise.

**Audience & WTP:** 16-28 year old core demographic with low-to-moderate disposable income. The betting segment has WTP but introduces different product considerations. Brand sponsorship is the dominant revenue model in the esports content market, not subscriptions.

**Curation complexity:** Low. Esports coverage is results-and-roster-driven; editorial judgment is fairly mechanical.

**Existing tools:** Dot Esports, ESPN Esports, Upcomer cover the space with dedicated editorial teams. Thegg.net, Dexerto. All ad-supported, no strong subscription model demonstrated.

**Fit score: 2/5.** The audience has low subscription WTP and the content model is ad-supported. The pipeline's RSS-to-digest structure fits technically, but there is no demonstrated subscription revenue ceiling to build toward.

---

### 7. Mining / Resources Industry

**Sources:** Mining.com RSS (well-maintained), International Mining (im-mining.com/feed), Mining Magazine, Engineering & Mining Journal (e-mj.com/feed), Kitco mining news, NS Energy, Stockhouse mining. 10+ quality RSS feeds exist.

**Volume:** 30-80 items/day across the sector; narrower (e.g., copper mining, gold royalties) is 10-30/day.

**Frequency:** Daily or 3x/week. Operational professionals check in frequently; investors need daily coverage of commodity price moves.

**Audience & WTP:** Mining company executives, project managers, and technical staff ($200-500/mo corporate budget); junior mining investors ($50-150/mo); equity analysts at resource funds ($500+/mo). B2B budgets are real and the audience is used to paying for trade intelligence.

**Curation complexity:** Moderate. The pipeline must distinguish commodity price moves, M&A, exploration results, and regulatory news. Geological/technical depth is sometimes required. Cross-jurisdiction regulatory tracking (Canada, Australia, DRC, Chile) adds complexity.

**Existing tools:** Mining.com and Mining Journal exist but are general news aggregators, not AI-curated digests. S&P Global provides commodity pricing data at institutional pricing. No strong AI-curated digest exists for this vertical.

**Fit score: 4/5.** Good RSS availability, B2B audience with genuine WTP, limited direct competition for an AI-curated digest. Volume is tractable. The clearest path to revenue outside the general news vertical.

---

### 8. Oil, Gas & Energy

**Sources:** S&P Global Energy (some free, API pricing), OilPrice.com RSS, Energy Monitor, Oil & Gas Journal (ogj.com), Reuters Energy, Bloomberg Energy (paywalled), Energy Intelligence. Mix of free and expensive sources.

**Volume:** 100-300 items/day across the full sector. Highly variable by sub-segment (upstream, midstream, LNG, energy transition).

**Frequency:** Daily. Commodity markets and geopolitics drive daily urgency.

**Audience & WTP:** Energy traders ($2,000+/mo Bloomberg/Platts already), upstream professionals ($200-1,000/mo), energy transition consultants ($100-300/mo), policy analysts ($50-200/mo). The highest-WTP tier (traders) is locked into expensive incumbent platforms with proprietary pricing data the pipeline can't replicate.

**Curation complexity:** High. Commodity pricing context is essential; a story about LNG supply means nothing without spot price context, which requires licensed data feeds. Geopolitical analysis is complex. Quality journalism exists behind expensive paywalls (Platts, Bloomberg Energy).

**Existing tools:** S&P Global Energy (Platts) charges institutional rates ($30,000+/yr). OilPrice.com is free and broad. Energy Monitor is quality but ad-supported. The premium tier is extremely well-served.

**Fit score: 3/5.** Technically feasible but the best-WTP audience already has incumbents with proprietary data advantages the pipeline can't match. The addressable gap is the mid-market ($100-500/mo) energy professional who can't justify Platts pricing -- a real segment but harder to differentiate.

---

### 9. Sustainability / ESG Consulting

**Sources:** ESG Today RSS, Responsible Investor, GreenBiz, Environmental Finance, SEC EDGAR (sustainability filings), EU sustainability regulation feeds (EUR-Lex RSS), CDP disclosures. Good free RSS availability.

**Volume:** 50-150 items/day across regulatory, corporate disclosure, and research angles.

**Frequency:** Weekly or 3x/week. Regulatory developments justify more frequent monitoring; strategy content is weekly.

**Audience & WTP:** ESG consultants and analysts at consulting firms ($200-500/mo corporate), corporate sustainability teams ($100-300/mo), institutional investors with ESG mandates ($200-500/mo), regulatory compliance staff ($100-400/mo). All segments have corporate budget, not personal spending.

**Curation complexity:** Moderate-to-high. Distinguishing genuine regulatory requirements from lobbying positioning, assessing scope and jurisdiction of new rules, and synthesising across frameworks (CSRD, TCFD, GRI, SEC climate disclosure) requires domain-configured prompting.

**Existing tools:** ESG Today is well-run but lightly curated. Responsible Investor is behind a paywall ($1,500+/yr). No strong AI-curated daily digest exists. The EU's CSRD alone brought 50,000+ firms into mandatory disclosure scope, creating a large and motivated professional audience.

**Fit score: 4/5.** Strong B2B WTP, growing regulatory complexity that makes curation genuinely valuable, limited AI-digest competition. The pipeline's Claude subagents are well-suited to regulatory synthesis across jurisdictions. Main risk: the space is attracting well-funded entrants.

---

### 10. Policy & Regulation (EU / US Federal)

**Sources:** EUR-Lex RSS (official EU legislation and regulations), European Parliament news RSS, European Commission press releases, Federal Register RSS (US), Congress.gov RSS, Politico Europe (partial RSS), The Hill, Axios, state government feeds. Rich official source coverage.

**Volume:** 100-300 items/day for a focused jurisdiction or policy domain; 300-1000+/day if cross-jurisdiction.

**Frequency:** Daily. Regulatory filings, committee hearings, and vote outcomes have daily impact on compliance obligations.

**Audience & WTP:** Lobbyists and government affairs staff ($5,000-15,000+/yr -- Politico Pro charges ~$10,000/yr); compliance officers ($2,000-5,000/yr); policy journalists ($500-1,000/yr institutional); trade association staff ($1,000-3,000/yr). The enterprise segment has the highest demonstrated WTP in any content category surveyed.

**Curation complexity:** High. Distinguishing a draft consultation from enacted legislation, tracking amendment stages, assessing regulatory scope and effective dates, and mapping across jurisdictions (EU Member States vs. EU-level) requires structured editorial logic. The pipeline would need domain-specific subagent instructions.

**Existing tools:** Politico Pro is the dominant player at ~$10,000/yr and demonstrates the WTP ceiling clearly. Bloomberg Government ($7,000-12,000/yr), FiscalNote, Quorum. These are expensive and focused on US Congressional tracking. EU-specific curation is less well-served at the mid-market price point.

**Fit score: 4/5.** The highest WTP of any vertical assessed, with official RSS feeds that are reliable and free. EU regulatory tracking is the most interesting gap -- Politico Pro is strong on US, weaker on EU, and the mid-market ($1,000-3,000/yr) team subscription is underserved. The pipeline maps naturally to "what changed in EU digital regulation this week."

---

### 11. Open Source / Developer Tools

**Sources:** Hacker News RSS (hnrss.org -- already in the pipeline), GitHub blog, dev.to RSS, Changelog podcast feed, npm/PyPI release feeds, GitHub release feeds per project, product changelogs via RSS. Excellent RSS availability.

**Volume:** 200-500 items/day across all sources. HN at >100 points alone is ~30-50 items/day.

**Frequency:** Daily. Developer news is consumed daily; TLDR has conditioned the audience.

**Audience & WTP:** Software developers and engineering managers. WTP is structurally low -- developers are accustomed to free news. TLDR generates $7-10M+/year revenue from sponsors at $18-30k per placement, not subscriptions. Individual subscription WTP is $0-10/mo.

**Curation complexity:** Moderate. The pipeline already runs HN through Claude; extending to GitHub releases and dev blogs is incremental. The editorial challenge is distinguishing genuinely novel tools from marketing. The pipeline's existing tech-adjacent tuning is an advantage.

**Existing tools:** TLDR dominates with 1.6M subscribers on the main newsletter and $10M+ revenue -- all from sponsorships. Changelog, Console (console.dev), DevDigest. This is the most crowded segment assessed, anchored by a scaled, profitable incumbent.

**Fit score: 2/5.** Technically the best fit for the existing pipeline (HN feed is already wired in), but the market structure is wrong for subscriptions. TLDR proves the audience is large but the revenue model must be sponsorship-based. Building a smaller version of TLDR is not an interesting business.

---

### 12. Investment Research / VC News

**Sources:** PitchBook (expensive API), Crunchbase RSS (limited free tier), AngelList announcements, SEC EDGAR filings RSS (8-K, S-1, 13F), Axios Pro Rata, The Information (partial RSS), Bloomberg VC coverage. Mix of free regulatory filings and expensive proprietary data.

**Volume:** 100-400 items/day across VC, PE, M&A, and public market news.

**Frequency:** Daily. Deal flow and market-moving news doesn't wait.

**Audience & WTP:** VC partners and analysts ($500-2,000/mo), startup founders tracking competitive funding ($50-200/mo), equity analysts ($200-1,000/mo), M&A advisors ($500-2,000/mo). WTP is genuine and the audience is comfortable with professional tool spending.

**Curation complexity:** High. Distinguishing a seed round from a Series B, assessing strategic significance of an acquisition, understanding valuation multiples in context, and tracking exit signals all require domain-configured editorial logic. SEC filings require parsing structured documents, not just RSS summaries.

**Existing tools:** The Information ($599/yr standard, $999/yr Pro) is the quality benchmark. Axios Pro Deals covers the deal flow. PitchBook ($25,000/yr for teams) dominates institutional. The mid-market ($100-300/mo) professional digest gap is real but contested.

**Fit score: 3/5.** The WTP is among the highest of the surveyed verticals and the content has daily urgency. The challenge is data sourcing -- the most valuable signals (deal flow, cap table data) are behind expensive proprietary walls. A pipeline built on free RSS + SEC EDGAR feeds could serve the $50-200/mo individual professional segment but won't compete with PitchBook for institutional buyers.

---

## Ranked Shortlist: Top 3 Verticals

### #1: Policy & Regulation (EU Focus)

The highest WTP in any vertical surveyed -- Politico Pro has normalised $10,000/yr enterprise subscriptions and the audience pays without complaint. The EU regulatory beat is specifically underserved relative to US Congressional tracking: EUR-Lex, European Parliament, and European Commission all publish free, reliable RSS feeds; the content volume is manageable (100-200 items/day for a focused domain like digital regulation or sustainability); and the pipeline's Claude subagents are exactly the right tool for synthesising multi-jurisdiction regulatory change into "here's what this means for your compliance team." The barrier to entry is domain expertise encoded in prompts, not proprietary data. Starting with EU digital regulation (AI Act, GDPR, DSA, DMA) -- a topic with global business impact -- and targeting legal, compliance, and government affairs teams at mid-size companies is a credible initial wedge.

### #2: Sustainability / ESG Consulting

The EU CSRD alone brought 50,000+ firms into mandatory sustainability disclosure scope. ESG consultants and corporate sustainability teams are paid to track exactly the kind of regulatory + corporate + research cross-stream synthesis this pipeline does naturally. WTP is $100-500/mo on a corporate card, which is well within range for a team subscription. Free tools (ESG Today, GreenBiz) exist but are shallow. The main risk is that large consulting firms (Deloitte, PwC) will build their own tools, but the mid-market (boutique consultancies, in-house ESG teams at industrial companies) is real and currently underserved.

### #3: Mining / Resources Industry

The most concrete near-term opportunity outside regulation. Mining professionals pay for trade intelligence, the RSS sources are good, volume is tractable, and there is no strong AI-curated digest in the market. A copper/gold/lithium-focused digest targeting junior mining investors and corporate development teams is the right initial scope. The audience is smaller than policy or ESG but extremely loyal and comfortable paying for specialist information. Junior mining is also a sector where being early to exploration news genuinely matters -- which creates urgency that drives subscription retention.

---

## Red Flags

**Open Source / Developer Tools:** The economics are structurally wrong. TLDR has already captured the audience at scale and runs a sponsorship model that individual publishers can't replicate without first acquiring hundreds of thousands of subscribers. Building "a smaller TLDR" is not a viable path.

**Economics Research:** The free tier is too strong (NBER Digest, VoxEU). The paying segment -- financial market economists -- is locked into Bloomberg and Refinitiv. No credible gap at addressable pricing.

**Niche Sports (Hockey / F1 / Esports):** The Athletic's 5M newsletter subscribers and dedicated vertical coverage (Red Light for hockey, F1 newsletter) means the space is institutionally covered. Competing requires original journalism and access, not just AI curation. The sports content the pipeline would curate is content The Athletic already packages.

**Investment Research / VC:** Sounds attractive because WTP is real, but the most differentiated content (deal flow, cap table data, founder relationships) lives behind PitchBook and proprietary data walls. A pipeline built on free RSS produces a commodity product compared to what The Information and Axios Pro already do with dedicated journalists.

**Patent Filings:** Not included in the ranked verticals for a specific reason. USPTO publishes weekly patent data as XML bulk files and offers a PatentsView API, but the editorial curation task -- assessing which patents signal competitive intent vs. defensive filing -- requires deep domain knowledge that's very hard to encode reliably. The audience (IP attorneys, competitive intelligence teams) is small, institutional, and already served by expensive specialised tools (Derwent Innovation, Anaqua). Volume is too high (10,000+ US patents granted weekly) and requires significant filtering infrastructure before Claude curation adds value.

**Job Market Signals:** Attractively framed -- "sector hiring trends as business intelligence" -- but the data sources are the problem. Meaningful hiring signal requires LinkedIn scraping or purchasing structured job posting data from providers like JobsPikr or Lightcast, which carry licensing costs and API complexity far outside the RSS feed model. Public RSS from job boards provides lagged, noisy data. Without proprietary sourcing, the pipeline produces a worse version of what BambooHR and Recruitics already publish quarterly for free.
