//! The "connect an AI tool" page: how to point your own assistant at this site's MCP server.
//!
//! The pattern is Temporal's docs, minus the part they pay for. Their "Use MCP" menu offers
//! Add to Claude Code / Codex / Cursor / VS Code / Copy URL, and behind it sits a vendor's
//! hosted server over a vendor's index. Ours points at our own endpoint, which answers from
//! the archive itself rather than from a retrieval approximation of it.
//!
//! Every command here is DERIVED from one MCP URL, so a domain change cannot leave a stale
//! command on the page, and the tool list comes from the router that serves them.

use super::chrome;
use super::digest::og_image_tags;
use crate::util::escape_html;

pub struct ConnectParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    /// Absolute URL of the JSON-RPC endpoint. Every command on the page is built from it.
    pub mcp_url: &'a str,
    /// Where a reader can read the same catalogue as text (the `GET /mcp` listing).
    pub listing_url: &'a str,
    /// The GET bridge's catalogue, for the reader who cannot connect a client at all.
    pub tools_url: &'a str,
    /// `(name, description)` per tool, from the live router so this cannot drift.
    pub tools: &'a [(String, String)],
}

/// Server name used in every generated command. Short, lowercase, and stable: it becomes the
/// key in someone else's config file, so changing it would orphan their existing entry.
pub const SERVER_KEY: &str = "news-digest";

const CONNECT_CSS: &str = r#"
.narrow{max-width:680px;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:20px 0 0;}
.body{font-family:var(--serif); font-size:16px; color:var(--muted); line-height:1.65; margin:16px 0 0;}
.opts{margin:30px 0 0; display:flex; flex-direction:column; gap:14px;}
.opt{border:1px solid var(--line); padding:14px 16px;}
.opt .oh{display:flex; align-items:baseline; justify-content:space-between; gap:12px;}
.opt .on{font-family:var(--sans); font-weight:700; font-size:13px; color:var(--ink); letter-spacing:.02em;}
.opt .od{font-family:var(--serif); font-size:13px; color:var(--muted);}
.opt pre{margin:10px 0 0; background:var(--code-bg,rgba(127,127,127,.08)); padding:10px 12px;}
.opt code{font-family:var(--mono,ui-monospace,SFMono-Regular,Menlo,monospace); font-size:12.5px;
  color:var(--ink2); white-space:pre-wrap; overflow-wrap:anywhere;}
.copy{font-family:var(--sans); font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  border:1px solid var(--line); background:none; color:var(--muted); padding:5px 10px; cursor:pointer;}
.copy:hover{color:var(--ink); border-color:var(--accent-ink);}
.copy[hidden]{display:none;}
.oneclick{font-family:var(--sans); font-size:12px; text-decoration:none; color:var(--accent-ink);
  border:1px solid var(--line); padding:8px 14px; display:inline-block; margin-top:10px;}
.tools{margin-top:36px; border-top:1px solid var(--line); padding-top:20px;}
.tools h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink); margin:0 0 12px;}
.tools dl{margin:0;}
.tools dt{font-family:var(--mono,ui-monospace,Menlo,monospace); font-size:13px; color:var(--ink); margin-top:12px;}
.tools dd{margin:3px 0 0; font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.5;}
@media (max-width:560px){ .opt .oh{flex-direction:column; align-items:flex-start;} }
"#;

/// Clipboard copy, progressively enhanced: the buttons are hidden in the markup and revealed
/// only if the API is actually usable, so a browser without it shows selectable text and no
/// button that would do nothing.
const COPY_JS: &str = r#"<script>(function(){
if(!navigator.clipboard)return;
document.querySelectorAll('.copy').forEach(function(b){
  b.hidden=false;
  b.addEventListener('click',function(){
    var pre=document.getElementById(b.getAttribute('data-for'));
    if(!pre)return;
    navigator.clipboard.writeText(pre.textContent).then(function(){
      var t=b.textContent;b.textContent='Copied';setTimeout(function(){b.textContent=t;},1400);
    });
  });
});
})();</script>"#;

/// One copyable command block.
fn option(id: &str, name: &str, note: &str, command: &str) -> String {
    format!(
        r#"<div class="opt"><div class="oh"><span class="on">{name}</span><span class="od">{note}</span></div>
<pre id="{id}"><code>{cmd}</code></pre>
<button class="copy" type="button" data-for="{id}" hidden>Copy</button></div>"#,
        cmd = escape_html(command)
    )
}

/// The Cursor one-click deep link. Format is Cursor's documented
/// `cursor://anysphere.cursor-deeplink/mcp/install?name=&config=<base64 JSON>`; the JSON is the
/// same remote-server shape an `mcp.json` entry uses.
pub fn cursor_link(mcp_url: &str) -> String {
    use base64::Engine;
    let config = format!(r#"{{"url":"{mcp_url}"}}"#);
    let encoded = base64::engine::general_purpose::STANDARD.encode(config);
    format!("cursor://anysphere.cursor-deeplink/mcp/install?name={SERVER_KEY}&config={encoded}")
}

/// The commands this page offers, derived from `mcp_url`. Public so a test can assert the
/// exact strings without rendering a page around them.
pub fn commands(mcp_url: &str) -> Vec<(&'static str, &'static str, String)> {
    vec![
        (
            "Claude Code",
            "Run in your terminal",
            format!("claude mcp add --transport http {SERVER_KEY} {mcp_url}"),
        ),
        (
            "Codex",
            "Run in your terminal",
            format!("codex mcp add {SERVER_KEY} --url {mcp_url}"),
        ),
        (
            "VS Code",
            "Run in your terminal",
            format!(
                r#"code --add-mcp '{{"name":"{SERVER_KEY}","type":"http","url":"{mcp_url}"}}'"#
            ),
        ),
        ("Any other client", "Paste this URL", mcp_url.to_string()),
    ]
}

pub fn render_connect(p: &ConnectParams) -> String {
    let head = chrome::page_head(
        p.title,
        "Connect this briefing's archive to Claude, ChatGPT, or any MCP client.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        CONNECT_CSS,
    );

    // Cursor is the one client with a working install URL, so it gets a link instead of a
    // command -- but in the same row shape as the rest, or it reads as a stray button.
    let cursor = format!(
        r#"<div class="opt"><div class="oh"><span class="on">Cursor</span><span class="od">One-click install</span></div>
<a class="oneclick" href="{}">Add to Cursor &rarr;</a></div>"#,
        escape_html(&cursor_link(p.mcp_url))
    );
    let mut opts = String::new();
    for (i, (name, note, cmd)) in commands(p.mcp_url).iter().enumerate() {
        opts.push_str(&option(&format!("c{i}"), name, note, cmd));
        if i == 1 {
            opts.push_str(&cursor);
        }
    }

    let tools: String = p
        .tools
        .iter()
        .map(|(name, desc)| {
            format!(
                "<dt>{}</dt><dd>{}</dd>",
                escape_html(name),
                escape_html(desc)
            )
        })
        .collect();

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <main id="main" class="narrow">
      <a class="brandmark" href="{home}">{brand}</a>
      <h1 class="h1">Connect your <em>assistant</em></h1>
      <p class="lede">This briefing publishes its archive as a Model Context Protocol server, so your own assistant can read it directly &mdash; every issue, the running story threads, the sources with their bias ratings, and the cost of each run.</p>
      <p class="body">Answers come from the archive itself, not from a search index built over it: ask for an issue by date and you get that issue. Read-only, public data, no key, no account.</p>
      <div class="opts">{opts}</div>
      <p class="body">No client? Every tool is also a plain URL &mdash; start at <a href="{tools_url}">the tool catalogue</a>, or read <a href="{listing_url}">the endpoint's own listing</a>.</p>
      <div class="tools">
        <h2>What your assistant can call</h2>
        <dl>{tools}</dl>
      </div>
    </main>
    {footer}
</div></div>
{copy_js}
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        home = p.home_url,
        brand = p.brand_html,
        tools_url = p.tools_url,
        listing_url = p.listing_url,
        footer = p.footer_html,
        copy_js = COPY_JS,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const URL: &str = "https://digest.example/mcp";

    fn params(tools: &'static [(String, String)]) -> ConnectParams<'static> {
        ConnectParams {
            title: "Test Digest",
            brand_html: "Test <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://digest.example",
            feed_url: "/feed.xml",
            image_url: "https://digest.example/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            mcp_url: URL,
            listing_url: "https://digest.example/mcp",
            tools_url: "https://digest.example/mcp/tools.json",
            tools,
        }
    }

    /// The exact strings a reader will paste into a terminal. Pinned because each is a
    /// third-party CLI contract verified against that CLI's own help output, not guessed:
    /// `claude mcp add [--transport http] <name> <url>` and
    /// `codex mcp add <NAME> (--url <URL> | -- <COMMAND>...)`.
    #[test]
    fn commands_are_the_verified_cli_syntax_and_carry_the_url() {
        let cmds = commands(URL);
        let joined: Vec<&str> = cmds.iter().map(|(_, _, c)| c.as_str()).collect();
        assert_eq!(
            joined,
            vec![
                "claude mcp add --transport http news-digest https://digest.example/mcp",
                "codex mcp add news-digest --url https://digest.example/mcp",
                r#"code --add-mcp '{"name":"news-digest","type":"http","url":"https://digest.example/mcp"}'"#,
                "https://digest.example/mcp",
            ]
        );
    }

    #[test]
    fn cursor_link_encodes_the_documented_remote_config() {
        use base64::Engine;
        let link = cursor_link(URL);
        let encoded = link.split("config=").nth(1).expect("config param");
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .expect("valid base64");
        assert_eq!(
            String::from_utf8(decoded).unwrap(),
            r#"{"url":"https://digest.example/mcp"}"#
        );
        assert!(
            link.starts_with("cursor://anysphere.cursor-deeplink/mcp/install?name=news-digest&")
        );
    }

    #[test]
    fn page_renders_every_command_the_tools_and_the_escape_hatches() {
        let tools = Box::leak(Box::new([(
            "get_latest_issue".to_string(),
            "The newest issue of the briefing".to_string(),
        )]));
        let html = render_connect(&params(tools));

        for (_, _, cmd) in commands(URL) {
            // The VS Code command carries quotes, so compare against the escaped form.
            assert!(html.contains(&escape_html(&cmd)), "missing command: {cmd}");
        }
        assert!(
            html.contains("cursor://anysphere.cursor-deeplink"),
            "{html}"
        );
        assert!(html.contains("get_latest_issue"), "{html}");
        assert!(html.contains("The newest issue of the briefing"), "{html}");
        assert!(
            html.contains("https://digest.example/mcp/tools.json"),
            "{html}"
        );
        // Copy buttons ship hidden and are revealed only where the clipboard API exists.
        assert!(
            html.contains(r#"class="copy" type="button" data-for="c0" hidden"#),
            "{html}"
        );
        assert!(html.contains("navigator.clipboard"), "{html}");
    }

    #[test]
    fn nothing_on_the_page_hardcodes_the_production_domain() {
        let tools: &'static [(String, String)] = &[];
        let html = render_connect(&params(tools));
        assert!(
            !html.contains("news-digest.seanfloyd.dev"),
            "hardcoded prod domain"
        );
    }
}
