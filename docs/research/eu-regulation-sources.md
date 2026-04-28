# EU Policy & Regulation Digest -- RSS Feed Inventory

Research date: 2026-04-03  
Purpose: Validate RSS source availability for an EU regulation vertical targeting compliance officers, legal/government affairs teams, and ESG consultants.

---

## 1. Already-Present EU Sources in Current Pipeline

Checked against `/newsroom/sources.json`. The following sources have partial EU coverage but are general-purpose, not EU-regulation-specific:

| Source | Perspective | EU Regulation Relevance |
|--------|-------------|------------------------|
| Der Spiegel (International) | German | Occasional EU policy coverage |
| Le Monde | French | Occasional EU institutional coverage |
| Economist Europe | Western/European | Macro EU economics and politics |
| Financial Times | Western finance | Some EU regulation coverage |
| The Guardian | Western/British | Occasional GDPR, digital regulation |
| The Verge | Tech | Some DSA/DMA coverage |
| Ars Technica | Tech | Some EU digital regulation |
| Reuters (via Google News) | Wire service | General EU coverage |

**Assessment:** None of the existing sources are dedicated EU regulation feeds. All are general-purpose. Zero duplication risk for a specialist vertical.

---

## 2. Verified Official EU Institution Feeds

All feeds below were tested with live HTTP requests.

### European Commission Press Corner
- **Feed URL:** `https://ec.europa.eu/commission/presscorner/api/rss`
- **Status:** VERIFIED
- **Content:** Press releases (IP), daily news summaries (MEX), Q&As (QANDA), commissioner speeches (SPEECH)
- **Items observed:** 10 items per fetch; covers all policy areas
- **Sample titles:** "Commission evaluation of the EU tobacco control framework...", "EU to deliver EUR 1.4 billion in revenue from immobilised Russian assets..."
- **Items/day estimate:** 8-15 (mixed topics; high-volume)
- **Notes:** Broad coverage -- needs topic filtering at curation stage. Does not require auth.

### European Parliament -- Press Releases (All Committees)
- **Feed URL:** `https://www.europarl.europa.eu/rss/doc/press-releases-committees/en.xml`
- **Status:** VERIFIED
- **Content:** Press releases from all EP committees including IMCO, LIBE, ITRE, JURI
- **Items observed:** 20 items; IMCO and LIBE committees both active on digital regulation
- **Sample titles:** "MEPs support postponement of certain rules on artificial intelligence", "MEPs travel to China... to focus on digital economy"
- **Items/day estimate:** 5-10
- **Notes:** Most relevant committee for EU regulation. Confirmed IMCO items on AI Act simplification.

### European Parliament -- Texts Adopted
- **Feed URL:** `https://www.europarl.europa.eu/rss/doc/texts-adopted/en.xml`
- **Status:** VERIFIED
- **Content:** Final legislative texts formally adopted by EP in plenary
- **Items observed:** 25 items; includes "Simplification of the implementation of harmonised rules on artificial intelligence (Digital Omnibus on AI)" (March 2026)
- **Items/day estimate:** 2-5 (lower volume, high signal)
- **Notes:** Authoritative legislative record. Very high signal for compliance tracking.

### European Parliament -- Internal Market & Industry (Topic 909)
- **Feed URL:** `https://www.europarl.europa.eu/rss/topic/909/en.xml`
- **Status:** VERIFIED
- **Content:** EP news on internal market, digital economy, AI regulation, cybersecurity, energy, consumer protection
- **Items observed:** 30 items
- **Sample titles:** "MEPs travel to China for first visit in eight years to focus on digital economy", "Deal on plans to boost Europe's Net-Zero technology production"
- **Items/day estimate:** 3-6

### European Parliament -- Top Stories (Priorities)
- **Feed URL:** `https://www.europarl.europa.eu/rss/doc/top-stories/en.xml`
- **Status:** VERIFIED
- **Content:** EP institutional priorities and flagship initiatives
- **Items observed:** 10 items (older content observed -- 2022-2023 cycle; appears infrequently updated)
- **Items/day estimate:** 1-3 (low volume, high-level only)
- **Notes:** Lower cadence. Good for major institutional shifts, not daily tracking.

### European Parliament -- Full RSS Feed Index
The EP publishes an extensive feed catalogue at `https://www.europarl.europa.eu/at-your-service/en/stay-informed/rss-feeds`. Other verified-working feeds for this vertical include:

| Feed | URL | Notes |
|------|-----|-------|
| Justice & citizenship (Topic 902) | `https://www.europarl.europa.eu/rss/topic/902/en.xml` | GDPR, rights, anti-corruption -- 30 items verified |
| Committee press releases | `https://www.europarl.europa.eu/rss/doc/press-releases-committees/en.xml` | All committees |
| Written questions | `https://www.europarl.europa.eu/rss/doc/written-questions/en.xml` | UNVERIFIED -- not tested |
| All plenary documents | `https://www.europarl.europa.eu/rss/doc/plenary/en.xml` | UNVERIFIED -- not tested |

### EDPB -- News
- **Feed URL:** `https://www.edpb.europa.eu/feed/news_en`
- **Status:** VERIFIED
- **Content:** EDPB news: enforcement cooperation, coordinated enforcement actions, GDPR guidance
- **Items observed:** 10 items
- **Sample titles:** "EDPB conference on cross-regulatory cooperation: what we learned", "CEF 2026: EDPB launches coordinated enforcement action on transparency and information obligations under the GDPR"
- **Items/day estimate:** 1-3 (lower volume, very high signal)

### EDPB -- Publications
- **Feed URL:** `https://www.edpb.europa.eu/feed/publications_en`
- **Status:** VERIFIED
- **Content:** EDPB formal outputs: opinions, case digests, joint opinions with EDPS, letters to Commission
- **Items observed:** 10 items
- **Sample titles:** "One-Stop-Shop case digest on the legal basis of 'legitimate interest'", "EDPB-EDPS Joint Opinion 4/2026 on the Proposal for a Cybersecurity Act 2 and NIS 2 amendments"
- **Items/day estimate:** 1-2 (authoritative GDPR interpretation)
- **Notes:** Extremely high signal for GDPR compliance tracking. Not daily -- burst on decision cycles.

### ENISA (EU Cybersecurity Agency)
- **Status:** UNVERIFIED -- multiple URL patterns attempted (404, 404). The ENISA website offers only email alert signup at `/alertservice`. No public RSS feed confirmed accessible.
- **Workaround:** Google News proxy feed for ENISA (see Section 3).

### EU AI Office
- **Status:** UNVERIFIED -- no dedicated feed found. The EU AI Office (part of the Commission) does not appear to maintain a standalone RSS feed as of April 2026. AI Act news comes through the Commission press corner and EP feeds.

### Official Journal of the EU (EUR-Lex)
- **Status:** UNVERIFIED for direct EUR-Lex RSS. Multiple EUR-Lex feed URL patterns returned empty content (not 404 -- blank body). EUR-Lex does maintain RSS infrastructure (`/RSSOJI.do`) but content was not returned in testing.
- **Workaround:** EUR-Lex supports custom alert subscriptions via web account (not public RSS). The EP Texts Adopted feed is a reliable proxy for significant legislation.

### European Council Press Releases
- **Status:** UNVERIFIED (403 Forbidden on all attempted feed URLs: `/en/press/press-releases/rss/`, `/en/rss/`, `/en/feed/?types=press-releases`). The Council website blocks feed access.
- **Workaround:** Google News proxy for Council press releases.

---

## 3. Verified Journalism & Analysis Feeds

### noyb (None Of Your Business)
- **Feed URL:** `https://noyb.eu/en/rss.xml`
- **Status:** VERIFIED
- **Content:** GDPR enforcement actions, complaints, court rulings, analysis of EU digital legislation
- **Items observed:** 11 items
- **Sample titles:** "Conseil d'Etat upholds Criteo's EUR 40M GDPR fine", "GDPR Omnibus: EU 'simplification' far removed from real business needs"
- **Items/day estimate:** 2-4
- **Bias:** Civil society / data subject rights perspective
- **Notes:** Gold-standard source for GDPR enforcement tracking. Covers noyb's own complaints (against Meta, Microsoft, TikTok) plus regulatory analysis.

### European Digital Rights (EDRi)
- **Feed URL:** `https://edri.org/feed/`
- **Status:** VERIFIED
- **Content:** EU digital rights advocacy: DSA, DMA, AI Act, surveillance, platform regulation
- **Items observed:** 20 items; includes biweekly EDRi-gram newsletter digest
- **Sample titles:** "Europe's digital laws are not bargaining chips", "EDRi-gram, 1 April 2026"
- **Items/day estimate:** 2-4
- **Bias:** Civil society / rights-based perspective
- **Notes:** Good signal for regulatory backstop -- covers pushback against weakening of EU digital laws.

### AlgorithmWatch
- **Feed URL:** `https://algorithmwatch.org/en/feed/`
- **Status:** VERIFIED
- **Content:** Investigative journalism on AI policy, automated decision-making, EU AI Act implementation
- **Items observed:** 10 items
- **Sample titles:** "Where the army does not use AI", "AI probably does lead to more computer security disasters"
- **Items/day estimate:** 1-3 (weekly cadence)
- **Bias:** Center-left / AI accountability
- **Notes:** Highest-quality AI governance coverage of any feed tested. Nonprofit, European-based.

### EUobserver
- **Feed URL:** `https://www.euobserver.com/rss`
- **Status:** VERIFIED
- **Content:** Independent EU political journalism covering governance, rule of law, defense, economics, migration
- **Items observed:** 20 items (general feed)
- **Sample titles:** "Spanish defiance or German deference: which one works better with Trump?", "Wartime scars at home: Ukraine confronts rising domestic abuse"
- **Items/day estimate:** 8-15
- **Bias:** Center; independent nonprofit
- **Notes:** Broader than pure regulation. Good for EU institutional context. Topic-specific feeds (`/rss/justice`, `/rss/tech`) appear to return HTML not XML -- general feed recommended.

### Statewatch
- **Feed URL:** `https://statewatch.org/news/rssfeed`
- **Status:** VERIFIED
- **Content:** Civil liberties watchdog covering EU surveillance, migration control, border tech, Frontex, facial recognition
- **Items observed:** 10 items
- **Sample titles:** "Frontex's European blackmail over migrants' 'voluntary' returns", "Border externalisation"
- **Items/day estimate:** 2-4
- **Bias:** Lean-left / civil liberties watchdog
- **Notes:** Niche but valuable for AI/biometrics regulation, NIS2 implementation, law enforcement data use.

### La Quadrature du Net
- **Feed URL:** `https://www.laquadrature.net/en/feed/`
- **Status:** VERIFIED
- **Content:** French digital rights NGO covering EU surveillance law, algorithmic accountability, GDPR litigation
- **Items observed:** 10 items
- **Sample titles:** "Censorship and surveillance: a legislative overload in the french parliament.", "CNAF's discriminatory scoring algorithm..."
- **Items/day estimate:** 1-3
- **Bias:** Lean-left / digital rights
- **Notes:** Strong on French DPA (CNIL) actions which set EU precedent.

### TechCrunch Europe
- **Feed URL:** `https://techcrunch.com/tag/europe/feed/`
- **Status:** VERIFIED
- **Content:** EU tech regulation, startup ecosystem, AI governance, DSA/DMA enforcement
- **Items observed:** 20 items
- **Sample titles:** "European Parliament blocks AI on lawmakers' devices, citing security risks", "One of Europe's largest universities knocked offline for days after cyberattack"
- **Items/day estimate:** 3-6
- **Bias:** Lean-left / US tech perspective
- **Notes:** US-based but reliable on EU regulatory developments affecting tech companies.

### Euractiv (via Google News proxy)
- **Feed URL:** `https://news.google.com/rss/search?q=site:euractiv.com+EU+regulation&hl=en-US&gl=US&ceid=US:en`
- **Status:** VERIFIED (50 items confirmed; direct euractiv.com blocked by WebFetch)
- **Content:** EU policy journalism: digital, environment, agriculture, trade, institutions
- **Sample titles:** "Editors' Choice: How bad could it get?", "Iran exposes Europe's strategic dependency on the US"
- **Items/day estimate:** 15-30 (high volume; all topics)
- **Notes:** Euractiv is the authoritative EU policy outlet. Direct RSS (`euractiv.com/section/tech/feed/`) is blocked by WebFetch but the Google News proxy works and is used by the existing pipeline (Reuters uses same pattern). High-volume -- consider topic-scoped query.

### Politico EU (via Google News proxy)
- **Feed URL:** `https://news.google.com/rss/search?q=site:politico.eu+tech+digital&hl=en-US&gl=US&ceid=US:en`
- **Status:** VERIFIED (100 items confirmed; direct politico.eu blocked)
- **Content:** EU tech and digital policy, AI Act, DMA, DSA, geopolitics
- **Sample titles:** "'Fatal decision': EU slammed for caving to US pressure on digital rules", "US pressures Brussels to join AI chips club"
- **Items/day estimate:** 10-20 on broad EU query
- **Notes:** Politico Europe is Pro-tier paywalled for most substantive pieces but free-tier headlines appear in Google News index.

### IAPP (via Google News proxy)
- **Feed URL:** `https://news.google.com/rss/search?q=site:iapp.org&hl=en-US&gl=US&ceid=US:en`
- **Status:** VERIFIED (100 items confirmed; direct iapp.org RSS returns 404)
- **Content:** Privacy professional news, GDPR enforcement, AI governance, global privacy law
- **Sample titles:** "IAPP Global Summit 2026 keynote: Prince Harry...", privacy certification and AI governance content
- **Items/day estimate:** 5-10
- **Notes:** IAPP is the standard reference for privacy professionals. Google News proxy captures public-facing content.

---

## 4. Coverage Gaps

### Well-covered topics (good RSS availability)
- **GDPR enforcement:** noyb, EDPB, Google News queries all deliver strong signal
- **AI Act / EU AI regulation:** EP feeds, AlgorithmWatch, TechCrunch Europe, Politico EU proxy
- **DSA/DMA:** EUobserver, TechCrunch, Google News DMA query (50 items verified)
- **General EU legislative activity:** EC press corner, EP committee feeds, EP texts adopted
- **Digital rights / civil liberties perspective:** EDRi, La Quadrature du Net, Statewatch, noyb
- **EU institutional news:** EP feeds, EUobserver, Euractiv proxy

### Poorly covered or inaccessible topics

**NIS2 / CSRD / ESG sustainability:**
- No dedicated outlet RSS found. Coverage comes through Google News queries (`NIS2 CSRD ESG regulation` -- 14 items/query). Sources are law firm blogs, consulting firms -- useful but not journalistic. Volume thin.
- EFRAG (EU financial reporting standards body) -- no public RSS found.

**Official Journal / legislative text tracking:**
- EUR-Lex direct RSS feed could not be confirmed functional. The OJ Legislative series (L) tracking -- which compliance officers need -- has no verified public feed.
- Workaround: EP Texts Adopted feed + Commission presscorner covers major items.

**National DPA enforcement decisions:**
- Individual DPA feeds (German BfDI, French CNIL, Spanish AEPD, Irish DPC) -- not tested; several attempted feeds returned errors. The EDPB feed aggregates some cross-border cases but not all national decisions.
- GDPR Hub (gdprhub.eu) -- 404 on attempted feed URL.

**Competition law / DMA enforcement specifics:**
- No dedicated competition regulator RSS. DG COMP press releases come through EC press corner (mixed in with all topics).
- Google News DMA query works (50 items verified) but is noisy.

**Paywalled / no-RSS outlets:**
- MLex -- professional subscription, no free RSS
- Politico EU Pro -- paywalled beyond headlines
- Financial Times -- blocks WebFetch
- Euractiv Pro newsletters -- paywalled
- IAPP direct feed -- 404; Google News proxy is the workaround

**EU Council (legislative negotiations / COREPER):**
- Council website returns 403 on all feed URLs. Significant gap -- Council trilogues and COREPER decisions are major compliance signals that have no direct feed.

**Volume summary:**
- Estimated total items/day from verified feeds: 60-120 items
- After EU-relevance filtering: 30-70 items
- After regulation-topic filtering: 15-40 items
- This is comfortably within the tractable 50-200/day range without needing aggressive filtering.

---

## 5. Recommended 20-Source Starter Set

Rationale for each selection: balanced between authoritative official feeds (high signal, low noise), quality journalism (context and interpretation), and watchdog/civil-society sources (early warning on enforcement and pushback).

| # | Source | Feed URL | Rationale |
|---|--------|----------|-----------|
| 1 | EC Press Corner | `https://ec.europa.eu/commission/presscorner/api/rss` | Official Commission communications -- primary legislative source |
| 2 | EP Committee Press Releases | `https://www.europarl.europa.eu/rss/doc/press-releases-committees/en.xml` | Committee activity including IMCO, LIBE on AI, GDPR, DSA |
| 3 | EP Texts Adopted | `https://www.europarl.europa.eu/rss/doc/texts-adopted/en.xml` | Final legislation -- definitive record |
| 4 | EP Internal Market (Topic 909) | `https://www.europarl.europa.eu/rss/topic/909/en.xml` | Digital economy, AI, cybersecurity, consumer protection |
| 5 | EP Justice & Citizenship (Topic 902) | `https://www.europarl.europa.eu/rss/topic/902/en.xml` | GDPR, rights, anti-corruption |
| 6 | EDPB News | `https://www.edpb.europa.eu/feed/news_en` | GDPR enforcement and cooperation -- gold standard |
| 7 | EDPB Publications | `https://www.edpb.europa.eu/feed/publications_en` | Formal GDPR opinions and guidance |
| 8 | noyb | `https://noyb.eu/en/rss.xml` | GDPR enforcement cases + critical analysis of Digital Omnibus |
| 9 | EDRi | `https://edri.org/feed/` | DSA/DMA/AI Act civil society perspective; biweekly digest |
| 10 | AlgorithmWatch | `https://algorithmwatch.org/en/feed/` | AI governance investigative journalism |
| 11 | EUobserver | `https://www.euobserver.com/rss` | Independent EU political journalism |
| 12 | Statewatch | `https://statewatch.org/news/rssfeed` | NIS2, biometric surveillance, law enforcement data |
| 13 | La Quadrature du Net | `https://www.laquadrature.net/en/feed/` | French DPA/CNIL angle; EU surveillance legislation |
| 14 | TechCrunch Europe | `https://techcrunch.com/tag/europe/feed/` | Accessible tech journalism on DSA/DMA/AI enforcement |
| 15 | Euractiv (Google News proxy) | `https://news.google.com/rss/search?q=site:euractiv.com+digital+regulation&hl=en-US&gl=US&ceid=US:en` | EU policy journalism -- topic-scoped query |
| 16 | Politico EU (Google News proxy) | `https://news.google.com/rss/search?q=site:politico.eu+tech+digital&hl=en-US&gl=US&ceid=US:en` | EU tech policy journalism |
| 17 | IAPP (Google News proxy) | `https://news.google.com/rss/search?q=site:iapp.org+EU&hl=en-US&gl=US&ceid=US:en` | Privacy professional perspective |
| 18 | Google News -- EU AI & Digital | `https://news.google.com/rss/search?q=EU+AI+Act+GDPR+DSA+DMA&hl=en-US&gl=US&ceid=US:en` | Broad aggregation -- catches law firm analysis, policy orgs |
| 19 | Google News -- EU CSRD/NIS2/ESG | `https://news.google.com/rss/search?q=EU+NIS2+CSRD+ESG+regulation&hl=en-US&gl=US&ceid=US:en` | Fills gap for sustainability and cybersecurity regulation |
| 20 | Google News -- DMA enforcement | `https://news.google.com/rss/search?q=EU+Digital+Markets+Act+DMA+enforcement&hl=en-US&gl=US&ceid=US:en` | DMA enforcement tracking -- 50 items verified |

**Notes on the Google News proxies:** The existing pipeline already uses this pattern for Reuters and Nikkei Asia. It is a legitimate workaround for paywalled or blocked direct feeds. Topic-scoped queries reduce noise. Three separate queries (AI/GDPR, CSRD/NIS2, DMA) split coverage across three feeds to keep per-source volume manageable.

---

## 6. Draft sources.json Entries

These entries follow the schema in `newsroom/sources.json`. The vertical adds two new fields not in the current schema: `vertical` (for multi-vertical routing) and `topics` (for curation hints). These fields are optional and backward-compatible -- the pipeline ignores unknown fields.

```json
[
  {
    "id": "eu_commission_press",
    "name": "European Commission Press Corner",
    "url": "https://ec.europa.eu/commission/presscorner/api/rss",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["legislation", "policy", "press_releases"]
  },
  {
    "id": "eu_parliament_committees",
    "name": "European Parliament -- Committee Press Releases",
    "url": "https://www.europarl.europa.eu/rss/doc/press-releases-committees/en.xml",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["legislation", "committees", "IMCO", "LIBE", "ITRE"]
  },
  {
    "id": "eu_parliament_texts_adopted",
    "name": "European Parliament -- Texts Adopted",
    "url": "https://www.europarl.europa.eu/rss/doc/texts-adopted/en.xml",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["legislation", "plenary", "adopted_law"]
  },
  {
    "id": "eu_parliament_internal_market",
    "name": "European Parliament -- Internal Market & Industry",
    "url": "https://www.europarl.europa.eu/rss/topic/909/en.xml",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["digital_economy", "AI", "cybersecurity", "consumer_protection"]
  },
  {
    "id": "eu_parliament_justice",
    "name": "European Parliament -- Justice & Citizenship",
    "url": "https://www.europarl.europa.eu/rss/topic/902/en.xml",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "rights", "rule_of_law", "anti_corruption"]
  },
  {
    "id": "edpb_news",
    "name": "EDPB News",
    "url": "https://www.edpb.europa.eu/feed/news_en",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "data_protection", "enforcement"]
  },
  {
    "id": "edpb_publications",
    "name": "EDPB Publications",
    "url": "https://www.edpb.europa.eu/feed/publications_en",
    "bias": "center",
    "factuality": "very-high",
    "perspective": "eu_institutional",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "opinions", "guidance", "data_protection"]
  },
  {
    "id": "noyb",
    "name": "noyb (None Of Your Business)",
    "url": "https://noyb.eu/en/rss.xml",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "civil_society",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "enforcement", "data_rights", "privacy"]
  },
  {
    "id": "edri",
    "name": "European Digital Rights (EDRi)",
    "url": "https://edri.org/feed/",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "civil_society",
    "vertical": "eu_regulation",
    "topics": ["DSA", "DMA", "AI_Act", "surveillance", "digital_rights"]
  },
  {
    "id": "algorithmwatch",
    "name": "AlgorithmWatch",
    "url": "https://algorithmwatch.org/en/feed/",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "civil_society",
    "vertical": "eu_regulation",
    "topics": ["AI", "algorithmic_accountability", "AI_Act", "automation"]
  },
  {
    "id": "euobserver",
    "name": "EUobserver",
    "url": "https://www.euobserver.com/rss",
    "bias": "center",
    "factuality": "high",
    "perspective": "european",
    "vertical": "eu_regulation",
    "topics": ["eu_politics", "rule_of_law", "governance", "digital"]
  },
  {
    "id": "statewatch",
    "name": "Statewatch",
    "url": "https://statewatch.org/news/rssfeed",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "civil_society",
    "vertical": "eu_regulation",
    "topics": ["surveillance", "biometrics", "NIS2", "law_enforcement_data", "Frontex"]
  },
  {
    "id": "laquadrature",
    "name": "La Quadrature du Net",
    "url": "https://www.laquadrature.net/en/feed/",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "civil_society",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "CNIL", "surveillance", "digital_rights"]
  },
  {
    "id": "techcrunch_europe",
    "name": "TechCrunch Europe",
    "url": "https://techcrunch.com/tag/europe/feed/",
    "bias": "lean-left",
    "factuality": "high",
    "perspective": "tech",
    "vertical": "eu_regulation",
    "topics": ["DSA", "DMA", "AI_Act", "tech_regulation", "startups"]
  },
  {
    "id": "euractiv_gn",
    "name": "Euractiv (via Google News)",
    "url": "https://news.google.com/rss/search?q=site:euractiv.com+digital+regulation&hl=en-US&gl=US&ceid=US:en",
    "bias": "center",
    "factuality": "high",
    "perspective": "european",
    "vertical": "eu_regulation",
    "topics": ["eu_policy", "digital", "regulation"]
  },
  {
    "id": "politico_eu_gn",
    "name": "Politico Europe -- Tech & Digital (via Google News)",
    "url": "https://news.google.com/rss/search?q=site:politico.eu+tech+digital&hl=en-US&gl=US&ceid=US:en",
    "bias": "center",
    "factuality": "high",
    "perspective": "european",
    "vertical": "eu_regulation",
    "topics": ["eu_tech_policy", "AI_Act", "DMA", "DSA"]
  },
  {
    "id": "iapp_eu_gn",
    "name": "IAPP EU Privacy News (via Google News)",
    "url": "https://news.google.com/rss/search?q=site:iapp.org+EU&hl=en-US&gl=US&ceid=US:en",
    "bias": "center",
    "factuality": "high",
    "perspective": "professional",
    "vertical": "eu_regulation",
    "topics": ["GDPR", "privacy", "data_protection", "compliance"]
  },
  {
    "id": "gn_eu_ai_gdpr",
    "name": "Google News -- EU AI & Digital Regulation",
    "url": "https://news.google.com/rss/search?q=EU+AI+Act+GDPR+DSA+DMA&hl=en-US&gl=US&ceid=US:en",
    "bias": "unrated",
    "factuality": "unrated",
    "perspective": "aggregated",
    "vertical": "eu_regulation",
    "topics": ["AI_Act", "GDPR", "DSA", "DMA", "digital_regulation"]
  },
  {
    "id": "gn_eu_csrd_nis2",
    "name": "Google News -- EU CSRD, NIS2 & ESG",
    "url": "https://news.google.com/rss/search?q=EU+NIS2+CSRD+ESG+regulation&hl=en-US&gl=US&ceid=US:en",
    "bias": "unrated",
    "factuality": "unrated",
    "perspective": "aggregated",
    "vertical": "eu_regulation",
    "topics": ["NIS2", "CSRD", "ESG", "sustainability", "cybersecurity"]
  },
  {
    "id": "gn_eu_dma",
    "name": "Google News -- DMA Enforcement",
    "url": "https://news.google.com/rss/search?q=EU+Digital+Markets+Act+DMA+enforcement&hl=en-US&gl=US&ceid=US:en",
    "bias": "unrated",
    "factuality": "unrated",
    "perspective": "aggregated",
    "vertical": "eu_regulation",
    "topics": ["DMA", "competition", "digital_markets", "enforcement"]
  }
]
```

---

## Summary Assessment

**Viability verdict: Yes, with caveats.**

A 20-source EU regulation vertical is viable today. The EP and EDPB feeds alone give authoritative legislative and enforcement signal. The civil-society layer (noyb, EDRi, AlgorithmWatch, Statewatch, La Quadrature) is unusually rich for a regulatory vertical -- these organisations track enforcement actions faster than mainstream press. Google News proxies fill gaps for paywalled outlets (Euractiv, Politico EU, IAPP) using the same pattern already validated in the current pipeline.

**Main gaps to flag to readers:**
1. No direct EU Council feed -- trilogues and COREPER decisions won't appear until EC or EP announce outcomes
2. No EUR-Lex OJ feed -- raw legislative text tracking requires manual checking or a separate alert service
3. NIS2/CSRD/ESG coverage is thin and dominated by law firm client alerts -- useful for compliance but lacks independent journalism

**Volume is tractable:** estimated 40-80 raw items/day after combining all 20 sources, dropping to 15-30 after topic relevance filtering. This is well within what the existing curation pipeline handles.
