//! The `/ask` page: a question box over the archive.
//!
//! Deliberately not a chat product. One column, a few example questions, and an answer that
//! names the issues it came from. The fine print names the model actually answering, read
//! from the live config so the disclosure cannot drift from the truth, and points at
//! `/connect` for the reader who would rather use their own client than ours.

use super::chrome;
use super::digest::og_image_tags;
use crate::util::escape_html;

pub struct AskParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub connect_url: &'a str,
    /// Absolute site origin. The client links ONLY URLs under it; see `ASK_JS`.
    pub origin: &'a str,
    /// The answering model, or `None` when no provider is configured (the box is then read-only).
    pub model: Option<&'a str>,
    pub provider: Option<&'a str>,
}

/// Example questions. Each is answerable from the tools and shows a different one off, so a
/// first-time reader learns the shape of what this can do by clicking rather than by reading.
const SUGGESTIONS: [&str; 4] = [
    "What was in today's briefing?",
    "What has the briefing covered about Iran?",
    "Which stories is it still following?",
    "How balanced are its sources?",
];

const ASK_CSS: &str = r#"
.narrow{max-width:680px;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:20px 0 0;}
.thread{margin:26px 0 0; display:flex; flex-direction:column; gap:26px;}
.turn{font-family:var(--serif); font-size:16px; line-height:1.65;}
.turn.q{color:var(--ink); font-family:var(--sans); font-size:14px; font-weight:600;
  padding-bottom:10px; border-bottom:1px solid var(--line);}
.turn.a{color:var(--ink2);}
.turn.a a{color:var(--accent-ink); text-decoration:none;
  background-image:linear-gradient(var(--accent-ink),var(--accent-ink));
  background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.ap{margin:0 0 12px;}
.ap:last-child,.al:last-child{margin-bottom:0;}
.ah{font-family:var(--sans); font-weight:700; font-size:12px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--ink); margin:18px 0 8px;}
.al{margin:0 0 12px; padding-left:18px;}
.al li{margin:0 0 6px; line-height:1.6;}
.turn.err{color:var(--accent-ink); font-size:15px;}
.steps{display:flex; flex-wrap:wrap; gap:8px; margin:0 0 10px;}
.step{font-family:var(--sans); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); border:0; padding:0;}
.step + .step::before{content:"· "; color:var(--line);}
.askform{display:flex; gap:10px; align-items:flex-end; margin:26px 0 0;}
.askin{flex:1; font-family:var(--serif); font-size:17px; color:var(--ink); background:none;
  border:0; border-bottom:1px solid var(--line); padding:10px 2px; resize:none; line-height:1.5;}
.askin:focus{outline:none; border-bottom-color:var(--accent-ink);}
.askin:focus-visible{outline:2px solid var(--accent-ink); outline-offset:4px;}
.asksend{font-family:var(--sans); font-size:13px; letter-spacing:.06em; text-transform:uppercase;
  border:0; border-bottom:1px solid var(--line); background:none; color:var(--muted);
  padding:10px 2px 11px; cursor:pointer;}
.asksend:hover:not(:disabled){border-color:var(--accent-ink); color:var(--accent-ink);}
.asksend:disabled{opacity:.5; cursor:default;}
.chips{display:flex; flex-wrap:wrap; gap:8px 22px; margin:20px 0 0;}
.chip{font-family:var(--serif); font-size:14px; color:var(--muted); background:none;
  border:0; border-bottom:1px solid var(--line); padding:4px 1px; cursor:pointer; text-align:left;}
.chip:hover{border-color:var(--accent-ink); color:var(--accent-ink);}
.fine{font-family:var(--serif); font-size:13.5px; color:var(--muted); line-height:1.6; margin:28px 0 0;
  border-top:1px solid var(--line); padding-top:16px;}
.fine code{font-family:var(--mono,ui-monospace,Menlo,monospace); font-size:12.5px;}
.off{border:1px solid var(--line); padding:14px 16px; margin:26px 0 0; font-family:var(--serif);
  font-size:15px; color:var(--muted);}
@media (max-width:560px){ .askform{flex-direction:column; align-items:stretch;} }
"#;

/// The client. Posts the question with the running history, reads the event stream, and shows
/// each tool as it runs. History lives here and nowhere else: the server stores nothing, and
/// each answer carries a signature that must be echoed back for the server to trust its own
/// words next turn.
const ASK_JS: &str = r#"<script>(function(){
var form=document.getElementById('askform');
if(!form)return;
var input=document.getElementById('askq'), send=document.getElementById('asksend'),
    thread=document.getElementById('thread'), chips=document.getElementById('chips');
var history=[], busy=false;

function el(cls,text){var d=document.createElement('div');d.className=cls;if(text!=null)d.textContent=text;return d;}

// Answers are Markdown. It is rendered by BUILDING NODES, never by assigning innerHTML, so
// nothing a model writes can become markup: every piece of text goes through createTextNode
// and every element is created explicitly. A subset is enough for what answers contain --
// headings, bold, lists, links, paragraphs -- and anything unrecognised stays literal text,
// which is the safe direction to be wrong in.
//
// Links: only URLs under OUR OWN origin become anchors. An answer is built from archive text
// a model wrote from news feeds, so a hostile item can put a URL in one, and it must not
// arrive styled as this site's own.
function inline(node,text){
  var origin=document.body.getAttribute('data-origin')||'';
  // [label](url) | **bold** | bare url
  var re=/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*|(https?:\/\/[^\s)\]]+)/g;
  var last=0,m;
  function link(url,label){
    if(origin&&url.indexOf(origin+'/')===0){
      var a=document.createElement('a');a.href=url;a.textContent=label;
      a.rel='nofollow ugc noreferrer';return a;
    }
    return document.createTextNode(label===url?url:label+' ('+url+')');
  }
  while((m=re.exec(text))!==null){
    if(m.index>last)node.appendChild(document.createTextNode(text.slice(last,m.index)));
    if(m[2]!==undefined){node.appendChild(link(m[2],m[1]));}
    else if(m[3]!==undefined){var b=document.createElement('strong');b.textContent=m[3];node.appendChild(b);}
    else{node.appendChild(link(m[4],m[4]));}
    last=m.index+m[0].length;
  }
  if(last<text.length)node.appendChild(document.createTextNode(text.slice(last)));
}

function renderAnswer(node,text){
  node.textContent='';
  var blocks=text.replace(/\r/g,'').split(/\n{2,}/);
  blocks.forEach(function(block){
    var lines=block.split('\n').filter(function(l){return l.trim()!=='';});
    if(!lines.length)return;
    var h=lines[0].match(/^(#{1,6})\s+(.*)$/);
    if(h&&lines.length===1){
      var el=document.createElement('h'+Math.min(h[1].length+2,6));
      el.className='ah';inline(el,h[2]);node.appendChild(el);return;
    }
    var bulleted=lines.every(function(l){return /^\s*[-*]\s+/.test(l);});
    var numbered=lines.every(function(l){return /^\s*\d+[.)]\s+/.test(l);});
    if(bulleted||numbered){
      var list=document.createElement(numbered?'ol':'ul');list.className='al';
      lines.forEach(function(l){
        var li=document.createElement('li');
        inline(li,l.replace(/^\s*(?:[-*]|\d+[.)])\s+/,''));
        list.appendChild(li);
      });
      node.appendChild(list);return;
    }
    var p=document.createElement('p');p.className='ap';
    inline(p,lines.join(' '));node.appendChild(p);
  });
}

function ask(q){
  if(busy||!q)return;
  busy=true;send.disabled=true;if(chips)chips.hidden=true;
  thread.appendChild(el('turn q',q));
  var steps=el('steps');thread.appendChild(steps);
  var out=el('turn a');thread.appendChild(out);
  out.textContent='Thinking…';
  var answered=false;

  fetch('/ask',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({question:q,history:history})}).then(function(r){
    if(!r.ok||!r.body){throw new Error(r.status===429?'Too many questions just now. Try again in a minute.':'That did not work.');}
    var reader=r.body.getReader(),dec=new TextDecoder(),buf='';
    function pump(){return reader.read().then(function(res){
      if(res.done)return finish();
      buf+=dec.decode(res.value,{stream:true});
      var parts=buf.split('\n\n');buf=parts.pop();
      parts.forEach(function(frame){
        // An answer with newlines arrives as SEVERAL data: lines; the spec says join them
        // with a newline. Concatenating instead collapses every list and paragraph.
        var ev='message',lines=[];
        frame.split('\n').forEach(function(line){
          if(line.indexOf('event:')===0)ev=line.slice(6).trim();
          else if(line.indexOf('data:')===0)lines.push(line.slice(5).replace(/^ /,''));
        });
        var data=lines.join('\n');
        if(ev==='tool'){steps.appendChild(el('step',data));}
        else if(ev==='answer'){answered=true;out.textContent='';renderAnswer(out,data);
          history.push({role:'user',content:q});history.push({role:'assistant',content:data});}
        else if(ev==='model'){var m=document.getElementById('askmodel');if(m&&data)m.textContent=data;}
        else if(ev==='failed'){answered=true;out.className='turn err';out.textContent=data;}
      });
      return pump();
    });}
    return pump();
  }).catch(function(e){
    out.className='turn err';out.textContent=e.message||'That did not work.';
  }).then(finish);

  function finish(){
    if(!answered&&out.textContent==='Thinking…'){out.className='turn err';out.textContent='No answer came back.';}
    busy=false;send.disabled=false;input.value='';input.focus();
  }
}

form.addEventListener('submit',function(e){e.preventDefault();ask(input.value.trim());});
input.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask(input.value.trim());}
});
if(chips)chips.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click',function(){ask(c.textContent);});
});
})();</script>"#;

pub fn render_ask(p: &AskParams) -> String {
    let head = chrome::page_head(
        p.title,
        "Ask a question about the briefing's archive; answers cite the issue they came from.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        ASK_CSS,
    );

    // No provider configured: say so instead of shipping a box that only ever fails.
    let Some(model) = p.model else {
        return format!(
            r#"{head}
<body data-origin="{origin}">
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <main id="main" class="narrow">
      <a class="brandmark" href="{home}">{brand}</a>
      <h1 class="h1">Ask the <em>archive</em></h1>
      <div class="off">The question box is not switched on for this deployment. You can still
      point your own assistant at the archive &mdash; see <a href="{connect}">connect your
      assistant</a>.</div>
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
            skip = chrome::SKIP_HTML,
            topbar = p.topbar_html,
            home = p.home_url,
            brand = p.brand_html,
            origin = escape_html(p.origin),
            connect = p.connect_url,
            footer = p.footer_html,
            toggle_js = chrome::TOGGLE_JS,
        );
    };

    let chips: String = SUGGESTIONS
        .iter()
        .map(|s| format!(r#"<button class="chip" type="button">{s}</button>"#))
        .collect();

    let provider = p
        .provider
        .map(|name| format!(" served by {}", escape_html(name)))
        .unwrap_or_default();

    format!(
        r#"{head}
<body data-origin="{origin}">
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <main id="main" class="narrow">
      <a class="brandmark" href="{home}">{brand}</a>
      <h1 class="h1">Ask the <em>archive</em></h1>
      <p class="lede">Every answer here is read out of the briefing's own archive by the same
      read-only tools any assistant can call, and cites the issue it came from. It has no
      opinions of its own and no knowledge beyond what has been published.</p>

      <div class="thread" id="thread" aria-live="polite"></div>

      <form class="askform" id="askform" novalidate>
        <label class="skip" for="askq">Your question about the briefing</label>
        <textarea class="askin" id="askq" rows="2" maxlength="2000"
          placeholder="What has the briefing said about&hellip;"></textarea>
        <button class="asksend" id="asksend" type="submit">Ask</button>
      </form>

      <div class="chips" id="chips" aria-label="Example questions">{chips}</div>

      <p class="fine">Answers are generated by <code id="askmodel">{model}</code>{provider}, from the archive's
      own search, issues, threads, sources and statistics. It can still be wrong, and the
      briefing it reads was itself written by a model &mdash; follow the issue links for what
      was actually published. Nothing you type is stored. Prefer your own assistant? See
      <a href="{connect}">connect your assistant</a>.</p>
    </main>
    {footer}
</div></div>
{ask_js}
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        home = p.home_url,
        brand = p.brand_html,
        origin = escape_html(p.origin),
        model = escape_html(model),
        connect = p.connect_url,
        footer = p.footer_html,
        ask_js = ASK_JS,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(model: Option<&'static str>) -> AskParams<'static> {
        AskParams {
            title: "Test Digest",
            brand_html: "Test <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://digest.example",
            feed_url: "/feed.xml",
            image_url: "https://digest.example/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            connect_url: "/connect",
            origin: "https://digest.example",
            model,
            provider: Some("Mistral"),
        }
    }

    #[test]
    fn a_configured_box_offers_the_form_and_names_the_model() {
        let html = render_ask(&params(Some("mistral-medium-2508")));
        assert!(html.contains(r#"id="askform""#), "{html}");
        assert!(
            html.contains(r#"<code id="askmodel">mistral-medium-2508</code>"#),
            "{html}"
        );
        assert!(html.contains("served by Mistral"), "{html}");
        for s in SUGGESTIONS {
            assert!(html.contains(s), "missing suggestion: {s}");
        }
        assert!(html.contains(r#"href="/connect""#), "{html}");
    }

    /// Without a provider the page must not ship a box that can only fail.
    #[test]
    fn an_unconfigured_box_says_so_and_offers_the_alternative() {
        let html = render_ask(&params(None));
        assert!(!html.contains(r#"id="askform""#), "{html}");
        assert!(html.contains("not switched on"), "{html}");
        assert!(html.contains(r#"href="/connect""#), "{html}");
    }

    /// An answer is built from archive text a model wrote from news feeds, so a hostile feed
    /// item can put a URL in one. Only our own origin may become a link.
    #[test]
    fn the_client_links_only_our_own_origin() {
        assert!(ASK_JS.contains("data-origin"), "{ASK_JS}");
        assert!(
            ASK_JS.contains("url.indexOf(origin+'/')===0"),
            "linkification must be scoped to our origin: {ASK_JS}"
        );
        assert!(ASK_JS.contains("nofollow ugc noreferrer"), "{ASK_JS}");
        let html = render_ask(&params(Some("m")));
        assert!(
            html.contains(r#"<body data-origin="https://digest.example">"#),
            "{html}"
        );
    }

    /// The provider can answer on a different model than the configured alias names, and the
    /// page said it would show that. It has to actually handle the event to do so.
    #[test]
    fn the_client_updates_the_disclosure_from_the_model_event() {
        assert!(ASK_JS.contains("ev==='model'"), "{ASK_JS}");
        assert!(ASK_JS.contains("askmodel"), "{ASK_JS}");
        let html = render_ask(&params(Some("m")));
        assert!(html.contains(r#"id="askmodel""#), "{html}");
    }

    /// The answer is model output. It reaches the DOM through textContent, never innerHTML,
    /// so an answer that contains markup is shown, not run.
    #[test]
    fn the_client_never_assigns_innerhtml() {
        // The property is that nothing is ASSIGNED to innerHTML; naming it in a comment that
        // explains why is fine, and the first version of this test failed on its own prose.
        assert!(
            !ASK_JS.contains("innerHTML="),
            "answers must not be parsed as markup"
        );
        assert!(
            !ASK_JS.contains("innerHTML ="),
            "answers must not be parsed as markup"
        );
        assert!(
            !ASK_JS.contains("insertAdjacentHTML"),
            "answers must not be parsed as markup"
        );
        assert!(ASK_JS.contains("createTextNode"), "{ASK_JS}");
    }
}
