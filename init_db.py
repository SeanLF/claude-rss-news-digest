#!/usr/bin/env python3
"""Initialize the news digest SQLite database with schema and sources."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "digest.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rss_url TEXT,
    bias TEXT,
    perspective TEXT,
    enabled BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    source_id TEXT REFERENCES sources(id),
    published_at DATETIME,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    summary TEXT,
    relevance_score REAL,
    category TEXT,
    is_noise BOOLEAN DEFAULT FALSE,
    story_thread_id INTEGER,
    story_status TEXT,
    presented_at DATETIME
);

CREATE TABLE IF NOT EXISTS story_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    article_count INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
CREATE INDEX IF NOT EXISTS idx_articles_presented ON articles(presented_at);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
"""

SOURCES = [
    ("al_jazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "center", "middle_east"),
    ("ars_technica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "center", "tech"),
    ("cbc_news", "CBC News", "https://www.cbc.ca/webfeed/rss/rss-world", "center", "canadian"),
    ("der_spiegel", "Der Spiegel", "https://www.spiegel.de/international/index.rss", "center-left", "german"),
    ("financial_times", "Financial Times", "https://www.ft.com/news-feed?format=rss", "center-right", "western_finance"),
    ("globe_and_mail", "Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/", "center", "canadian"),
    ("hacker_news", "Hacker News", "https://hnrss.org/newest?points=100", "center", "tech"),
    ("le_monde", "Le Monde", "https://www.lemonde.fr/rss/une.xml", "center", "french"),
    ("nyt_world", "NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "center-left", "american"),
    ("propublica", "ProPublica", "https://www.propublica.org/feeds/propublica/main", "center-left", "investigative"),
    ("rest_of_world", "Rest of World", "https://restofworld.org/feed/latest", "center", "global_tech"),
    ("reuters", "Reuters", "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en", "center", "wire_service"),
    ("scmp_asia", "SCMP Asia", "https://www.scmp.com/rss/2/feed", "center", "asian"),
    ("scmp_china", "SCMP China", "https://www.scmp.com/rss/4/feed", "center", "asian"),
    ("scmp_world", "SCMP World", "https://www.scmp.com/rss/5/feed", "center", "asian"),
    ("the_economist", "The Economist", "https://rss.app/feeds/9tqWs1xrWkLAfbEU.xml", "center-right", "western"),
    ("the_guardian", "The Guardian", "https://www.theguardian.com/international/rss", "center-left", "western"),
    ("the_intercept", "The Intercept", "https://theintercept.com/feed/?rss", "left", "investigative"),
    ("the_verge", "The Verge", "https://www.theverge.com/rss/index.xml", "center-left", "tech"),
    ("washington_post", "Washington Post", "https://feeds.washingtonpost.com/rss/world", "center-left", "american"),
    ("wsj_world", "WSJ World", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "center-right", "american"),
]


def init_db():
    """Create database and seed with sources."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create schema
    cursor.executescript(SCHEMA)

    # Seed sources (upsert)
    for source in SOURCES:
        cursor.execute("""
            INSERT OR REPLACE INTO sources (id, name, rss_url, bias, perspective)
            VALUES (?, ?, ?, ?, ?)
        """, source)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")
    print(f"Seeded {len(SOURCES)} news sources")


if __name__ == "__main__":
    init_db()
