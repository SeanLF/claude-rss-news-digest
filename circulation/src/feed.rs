//! Atom 1.0 feed generation for the digest archive.

use crate::util::{escape_html, format_date};

/// A single digest row as read from the `digests` table, used to build one feed entry.
pub struct DigestRow {
    pub date: String,
    pub preheader: String,
}

/// Render an Atom 1.0 feed listing `rows` (expected newest-first).
///
/// `base_url` is the scheme+host (e.g. "https://example.com"), or an empty string when
/// DIGEST_DOMAIN is unset (local/dev) -- links then fall back to root-relative paths.
/// `updated` is the feed-level `<updated>` timestamp (RFC 3339).
pub fn render_atom_feed(
    feed_name: &str,
    base_url: &str,
    updated: &str,
    rows: &[DigestRow],
) -> String {
    let feed_id = format!("{base_url}/");
    let self_url = format!("{base_url}/feed.xml");

    let mut entries = String::new();
    for row in rows {
        let escaped_date = escape_html(&row.date);
        let link = format!("{base_url}/{escaped_date}");
        let title = escape_html(&format!("{feed_name} \u{2013} {}", format_date(&row.date)));
        let entry_updated = format!("{escaped_date}T00:00:00Z");
        let summary = if row.preheader.is_empty() {
            String::new()
        } else {
            format!("\n    <summary>{}</summary>", escape_html(&row.preheader))
        };
        entries.push_str(&format!(
            r#"
  <entry>
    <title>{title}</title>
    <id>{link}</id>
    <link rel="alternate" type="text/html" href="{link}"/>
    <updated>{entry_updated}</updated>{summary}
  </entry>"#
        ));
    }

    format!(
        r#"<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <id>{feed_id}</id>
  <updated>{updated}</updated>
  <author>
    <name>{title}</name>
  </author>
  <link rel="self" type="application/atom+xml" href="{self_url}"/>
  <link rel="alternate" type="text/html" href="{feed_id}"/>{entries}
</feed>
"#,
        title = escape_html(feed_name),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(date: &str, preheader: &str) -> DigestRow {
        DigestRow {
            date: date.to_string(),
            preheader: preheader.to_string(),
        }
    }

    #[test]
    fn renders_well_formed_xml_with_entries() {
        let rows = vec![
            row("2026-06-12", "Second story of the day"),
            row("2026-06-11", "First story of the day"),
        ];
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &rows,
        );

        assert!(xml.starts_with(r#"<?xml version="1.0" encoding="utf-8"?>"#));
        assert!(xml.contains(r#"<feed xmlns="http://www.w3.org/2005/Atom">"#));
        assert_eq!(xml.matches("<entry>").count(), 2);
        assert_eq!(xml.matches("</entry>").count(), 2);
        // Newest-first order preserved as given.
        let first_entry_pos = xml.find("2026-06-12").unwrap();
        let second_entry_pos = xml.find("2026-06-11").unwrap();
        assert!(first_entry_pos < second_entry_pos);
    }

    #[test]
    fn feed_metadata_uses_base_url_and_updated() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[],
        );

        assert!(xml.contains("<id>https://example.com/</id>"));
        assert!(xml.contains("<updated>2026-06-12T00:00:00Z</updated>"));
        assert!(xml.contains(
            r#"<link rel="self" type="application/atom+xml" href="https://example.com/feed.xml"/>"#
        ));
        assert!(
            xml.contains(r#"<link rel="alternate" type="text/html" href="https://example.com/"/>"#)
        );
    }

    #[test]
    fn entry_link_and_id_point_at_the_digest_view_url() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "")],
        );

        assert!(xml.contains("<id>https://example.com/2026-06-12</id>"));
        assert!(xml.contains(
            r#"<link rel="alternate" type="text/html" href="https://example.com/2026-06-12"/>"#
        ));
        assert!(xml.contains("<updated>2026-06-12T00:00:00Z</updated>"));
    }

    #[test]
    fn omits_summary_when_preheader_is_empty() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "")],
        );

        assert!(!xml.contains("<summary>"));
    }

    #[test]
    fn includes_summary_when_preheader_present() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "Something happened today")],
        );

        assert!(xml.contains("<summary>Something happened today</summary>"));
    }

    #[test]
    fn escapes_feed_title_special_characters() {
        let xml = render_atom_feed(
            "News & Views Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[],
        );

        assert!(xml.contains("<title>News &amp; Views Digest</title>"));
        assert!(!xml.contains("News & Views Digest</title>"));
    }

    #[test]
    fn includes_feed_level_author_required_by_rfc_4287() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[],
        );

        assert!(xml.contains("<author>"));
        assert!(xml.contains("<name>News Digest</name>"));
        assert!(xml.contains("</author>"));
    }

    #[test]
    fn escapes_feed_level_author_name_special_characters() {
        let xml = render_atom_feed(
            "News & Views Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[],
        );

        assert!(xml.contains("<name>News &amp; Views Digest</name>"));
    }

    #[test]
    fn escapes_entry_summary_and_title_special_characters() {
        let xml = render_atom_feed(
            "News Digest",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "A <script> tag & an ampersand")],
        );

        assert!(!xml.contains("<script>"));
        assert!(xml.contains("&lt;script&gt;"));
        assert!(xml.contains("A &lt;script&gt; tag &amp; an ampersand"));
    }

    #[test]
    fn escapes_entry_title_when_feed_name_has_special_characters() {
        let xml = render_atom_feed(
            "News <Digest>",
            "https://example.com",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "")],
        );

        // Entry <title> is built from feed_name + date -- must be escaped there too,
        // independent of the top-level <feed><title> escaping.
        let entry_start = xml.find("<entry>").expect("entry present");
        let entry = &xml[entry_start..];
        assert!(!entry.contains("News <Digest>"));
        assert!(entry.contains("News &lt;Digest&gt;"));
    }

    #[test]
    fn falls_back_to_root_relative_links_without_base_url() {
        let xml = render_atom_feed(
            "News Digest",
            "",
            "2026-06-12T00:00:00Z",
            &[row("2026-06-12", "")],
        );

        assert!(xml.contains("<id>/2026-06-12</id>"));
        assert!(xml.contains(r#"href="/2026-06-12"/>"#));
    }
}
