//! Feedback thanks page - shown after a per-story up/down vote is recorded.

use super::digest::{FAVICON_SVG, REDUCED_MOTION_CSS, SKIP_LINK_CSS, SKIP_LINK_HTML};
use crate::util::escape_html;

/// Render the "thanks" confirmation page after a feedback vote is recorded.
///
/// `story` is shown (escaped) as a small confirmation detail so a reader who
/// double-checks the link sees which story their vote was recorded against.
pub fn render_feedback_thanks(digest_name: &str, date: &str, story: &str) -> String {
    let name = escape_html(digest_name);
    let date_esc = escape_html(date);
    let story_esc = escape_html(story);
    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Thanks – {name}</title>
  {favicon}
  <style>
    :root {{
      --bg: #fafaf8;
      --text: #1c1c1a;
      --text-muted: #6b6b67;
      --ruby: #c45a3b;
      --border: #e0e0da;
      color-scheme: light dark;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #141412;
        --text: #e6e6e2;
        --text-muted: #9a9a94;
        --ruby: #e07a5f;
        --border: #2c2c28;
      }}
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    html {{ font-size: 18px; background-color: var(--bg); }}
    body {{
      color: var(--text);
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.58;
      margin: 0;
      padding: 0;
    }}
    a {{ color: var(--ruby); }}
    .container {{
      max-width: 480px;
      margin: 0 auto;
      padding: 4.5rem 1.5rem 7rem;
      text-align: center;
    }}
    h1 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 1.5rem;
      margin: 0 0 0.75rem;
      color: var(--text);
    }}
    p {{ color: var(--text-muted); }}
    .feedback-detail {{
      font-size: 0.8rem;
      margin-top: 2rem;
    }}
    .feedback-detail code {{
      color: var(--text-muted);
    }}
    {skip_link_css}
    {reduced_motion_css}
  </style>
</head>
<body>
  {skip_link_html}
  <main id="main">
  <div class="container">
    <h1>Thanks — feedback recorded</h1>
    <p><a href="/{date_esc}">Back to today's digest</a></p>
    <p class="feedback-detail">Story: <code>{story_esc}</code></p>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        skip_link_html = SKIP_LINK_HTML,
        skip_link_css = SKIP_LINK_CSS,
        reduced_motion_css = REDUCED_MOTION_CSS,
    )
}
