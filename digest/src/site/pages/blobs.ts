// The pages' CSS and script bodies, carried over verbatim from circulation/src/templates (the design and
// its a11y and Lighthouse tuning live in them). Scripts are bodies without their <script> tag; the CSP
// allows each by its hash (security.ts).

export const chromeCss = String.raw`
*{box-sizing:border-box;}
/* on html so rubber-band overscroll past the top/bottom shows the paper ground, not white */
html{background:var(--paper);}
body{margin:0;}
.wrap{background:var(--paper); color:var(--ink); font-family:var(--serif);
  -webkit-font-smoothing:antialiased; min-height:100vh; padding:28px 32px 72px; hanging-punctuation:first last;}
.col{max-width:820px; margin:0 auto;}
a{color:var(--accent-ink);}
.btn{font-family:var(--sans); font-size:13px; font-weight:600; padding:10px 18px; cursor:pointer;
  border:1px solid; border-radius:var(--r-input); white-space:nowrap;}
.btn.primary{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
@media (prefers-color-scheme:dark){ .btn.primary{background:var(--accent); color:#16150f; border-color:var(--accent);} }
:root[data-theme="dark"] .btn.primary{background:var(--accent); color:#16150f; border-color:var(--accent);}
:root[data-theme="light"] .btn.primary{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
.btn.secondary{background:none; color:var(--accent-ink); border-color:var(--line-strong);}
.btn.secondary:hover{border-color:var(--accent); background:var(--wash);}
.btn:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

/* top utility bar */
.topbar{display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:30px; flex-wrap:wrap;}
.topnav{font-family:var(--sans); font-size:13px; display:flex; align-items:center;}
.topnav a{color:var(--muted); text-decoration:none;}
.topnav a:hover{color:var(--accent-ink);}
.topnav .sep{color:var(--line-strong); padding:0 10px;}
.topright{display:flex; align-items:center; gap:12px; font-family:var(--sans); font-size:12px;}
.topright a.sublink{font-family:var(--sans); font-size:12px; color:var(--muted); text-decoration:none;}
.topright a.sublink:hover{color:var(--accent-ink);}
.topright a.sublink:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
.pill{display:inline-flex; align-items:center; gap:6px; color:var(--accent-ink); border:1px solid var(--accent);
  border-radius:999px; padding:5px 12px; text-decoration:none; line-height:normal;}
.pill .g{font-family:var(--serif);}
.toggle{font-family:var(--sans); font-size:12px; color:var(--muted); background:none; border:1px solid var(--line);
  border-radius:var(--r-input); padding:6px 10px; cursor:pointer; line-height:normal;
  display:inline-flex; align-items:center; gap:6px; min-height:24px;}
.toggle:hover{color:var(--accent-ink); border-color:var(--accent);}
.toggle:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
/* two stacked glyphs: current (rest) crossfades to next-state (hover) — a visual echo of the
   "Switch to {next}" title. At rest only .tg-cur shows, so the control still reads as status. */
.tglyphs{position:relative; display:inline-flex; flex:none; width:1em; height:1em; font-size:13px; line-height:1;}
.tglyph{position:absolute; inset:0; display:flex; align-items:center; justify-content:center; line-height:1;
  transition:opacity .2s ease, transform .2s ease;}
.tg-next{opacity:0; transform:translateY(35%);}
.toggle:hover .tg-cur{opacity:0; transform:translateY(-35%);}
.toggle:hover .tg-next{opacity:1; transform:translateY(0);}
@media (max-width:420px){ .tword{display:none;} }

/* footer */
.site-foot{margin-top:44px; border-top:1px solid var(--line); padding-top:18px; font-family:var(--sans);
  font-size:12px; color:var(--muted); display:flex; flex-direction:column; gap:6px;}
.site-foot a{color:var(--muted); text-decoration:none; display:inline-block; padding:6px 0;}
.site-foot a:hover{color:var(--accent-ink);}
.site-foot .row{display:flex; flex-wrap:wrap; align-items:center;}
.site-foot .row .sep{color:var(--line-strong); padding:0 8px;}

/* masthead — shared: the index's big brand-as-h1 (.brand) and the sub-pages' eyebrow+title
   (.brandmark + .h1). The .masthead/.sub/.kicker/.stat frame is identical for both. */
.masthead{border-bottom:2px solid var(--ink); padding-bottom:16px;}
.brand{font-family:var(--serif); font-weight:600; font-size:38px; letter-spacing:-.018em; margin:0; line-height:1.02;}
.brand em{color:var(--accent-ink); font-style:normal;}
.brandmark{display:inline-block; font-family:var(--serif); font-size:15px; font-weight:600; color:var(--ink2);
  text-decoration:none; letter-spacing:-.005em; margin-bottom:5px;}
.brandmark em{color:var(--accent-ink); font-style:normal;}
.brandmark:hover{color:var(--ink);}
.h1{font-family:var(--serif); font-weight:600; font-size:34px; letter-spacing:-.018em; margin:0; line-height:1.04;}
.sub{display:flex; align-items:baseline; justify-content:space-between; gap:20px; margin-top:12px;}
.kicker{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);}
.stat{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap;}
.stat b{color:var(--ink2); font-weight:600;}

@media (max-width:560px){ .wrap{padding:22px 18px 56px;} .brand{font-size:29px;} .h1{font-size:27px;} .sub{flex-direction:column; gap:6px;} }

/* skip link — visible only when focused */
.skip{position:absolute; left:-9999px; top:0; z-index:10; background:var(--accent-ink); color:#fff;
  font-family:var(--sans); font-size:13px; padding:8px 14px; border-radius:var(--r-input);}
.skip:focus{left:8px; top:8px;}

@media (prefers-reduced-motion:reduce){ *,*::before,*::after{transition-duration:.01ms !important;} }
`;

export const toggleJs = String.raw`(function(){
  var btn=document.getElementById('themeBtn');if(!btn)return;
  var root=document.documentElement,ORDER=['system','light','dark'],
      ICON={system:'◐',light:'☀',dark:'☾'},WORD={system:'System',light:'Light',dark:'Dark'};
  function cur(){try{var t=localStorage.getItem('theme');return(t==='light'||t==='dark')?t:'system';}catch(e){return'system';}}
  function paint(s){var gc=btn.querySelector('.tg-cur'),gn=btn.querySelector('.tg-next'),w=btn.querySelector('.tword');
    var nx=ORDER[(ORDER.indexOf(s)+1)%3];
    if(gc)gc.textContent=ICON[s];if(gn)gn.textContent=ICON[nx];if(w)w.textContent=WORD[s];
    btn.setAttribute('aria-label','Theme: '+WORD[s]+'. Activate to switch to '+WORD[nx]+'.');
    btn.setAttribute('title','Switch to '+WORD[nx]);}
  function apply(s){if(s==='system'){root.removeAttribute('data-theme');try{localStorage.removeItem('theme');}catch(e){}}
    else{root.setAttribute('data-theme',s);try{localStorage.setItem('theme',s);}catch(e){}}paint(s);}
  btn.addEventListener('click',function(){apply(ORDER[(ORDER.indexOf(cur())+1)%3]);});
  try{var mq=window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener&&mq.addEventListener('change',function(){if(cur()==='system')paint('system');});}catch(e){}
  paint(cur());
})();`;

export const indexCss = String.raw`
/* notice bar (subscribe success/error) — semantic status axis, redundant glyph+word */
.notice{font-family:var(--sans); font-size:14px; padding:10px 14px; border-radius:var(--r-input);
  background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--muted);
  margin:20px 0 0; display:flex; gap:9px; align-items:baseline;}
.notice .ni{font-family:var(--mono);}
.notice.ok{border-left-color:var(--ok); color:var(--ok-ink);}
.notice.warn{border-left-color:var(--warn); color:var(--warn-ink);}
.notice.bad{border-left-color:var(--accent); color:var(--accent-ink);}

/* toolbar */
.toolbar{display:flex; gap:12px; align-items:center; margin:24px 0 4px; flex-wrap:wrap;}
.search{flex:1; min-width:200px; display:flex;}
.search input{flex:1; font-family:var(--sans); font-size:15px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:10px 13px; min-width:0;}
.search input::placeholder{color:var(--muted);}
.search input:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}
.seg{display:inline-flex; gap:2px; background:var(--wash); border:1px solid var(--line); border-radius:var(--r-input); padding:3px;}
.seg button{font-family:var(--sans); font-size:12px; color:var(--muted); background:transparent; border:1px solid transparent;
  padding:6px 14px; cursor:pointer; border-radius:4px;}
.seg button:hover{color:var(--ink2);}
.seg button[aria-pressed="true"]{background:var(--panel); color:var(--ink); font-weight:600; border-color:var(--line-strong); box-shadow:0 1px 2px rgba(25,25,23,.10);}
.seg button:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

/* browse row: period segment + date jump grouped (search stands alone above — it's a
   different action, a full-text jump to /search, not a list-scope control) */
.browse{display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-top:12px;}

/* date jump — pushed to the row's right; the segment sits at the left */
.datejump{display:inline-flex; align-items:center; gap:8px; font-family:var(--sans); font-size:12px; color:var(--muted); margin-left:auto;}
.datejump input{font-family:var(--sans); font-size:12px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:5px 8px; min-height:24px;}
.datejump input:focus-visible{outline:2px solid var(--accent); outline-offset:1px; border-color:var(--accent);}

/* list header + rows */
.listhead{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; padding:16px 8px 8px;
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  border-bottom:1px solid var(--line-strong);}
.listhead .r{text-align:right;}
ul.index{list-style:none; margin:0; padding:0;}
li.month{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink); padding:22px 0 8px; border-bottom:1px solid var(--ink);}
li.issue{border-bottom:1px solid var(--line);}
li.issue > a{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; align-items:baseline;
  padding:14px 8px; text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
li.issue > a:hover{background:var(--wash);}
li.issue.today > a{background:var(--accent-wash);}
li.issue > a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.idx{display:flex; flex-direction:column; gap:2px; white-space:nowrap;}
.idx .no{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; font-weight:400; order:1;}
.idx .no::before{content:"\00b7\00a0#"; font-weight:400; color:var(--line-strong);}
.idx .date{font-family:var(--mono); font-size:14px; color:var(--ink2); font-variant-numeric:tabular-nums; font-weight:600; order:0;}
.main .sumline{font-family:var(--serif); font-size:17px; font-weight:500; color:var(--ink); line-height:1.4; text-wrap:pretty; display:block; letter-spacing:-.004em;}
li.issue > a:hover .sumline{color:var(--accent-ink);}
.rt{display:flex; flex-direction:column; align-items:flex-end; gap:6px; white-space:nowrap;}
.count{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
.bias{display:inline-flex; height:7px; width:52px; border-radius:2px; overflow:hidden;}
.bias i{display:block; height:100%;}
.bias .l{background:var(--bias-l);} .bias .c{background:var(--bias-c);} .bias .r{background:var(--bias-r);}

/* load-more region + states */
.loadmore{text-align:center; margin:32px 0 0; display:flex; flex-direction:column; align-items:center; gap:10px;}
.loadmore-status{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0; min-height:1em;}
.loadmore.is-error .loadmore-status{color:var(--accent-ink);}
.loadmore-end{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0;}
.empty{font-family:var(--serif); font-size:18px; line-height:1.6; color:var(--muted); max-width:60ch; margin:28px 0 0;}

/* loading skeleton — calm alpha pulse, no shimmer */
.skeleton{border-bottom:1px solid var(--line);}
.skeleton .row{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; padding:14px 8px;}
.skeleton .bar{display:block; height:12px; border-radius:3px; background:var(--wash); animation:skel-pulse 1s ease-in-out infinite;}
.skeleton .bar-date{width:44px;} .skeleton .bar-line{width:80%;} .skeleton .bar-bias{width:52px; height:7px;}
@keyframes skel-pulse{0%,100%{opacity:.55} 50%{opacity:1}}
@media (prefers-reduced-motion:reduce){ .skeleton .bar{animation:none; opacity:.7;} }

/* subscribe band — CTA at the end, not a top form-wall */
.subband{display:flex; align-items:center; gap:40px; flex-wrap:wrap; margin-top:40px; padding:24px 28px; background:var(--panel-2); border:1px solid var(--line); border-radius:var(--r-card);}
.subband .copy{flex:0 1 auto; max-width:42ch;}
.subband .copy h2{font-family:var(--serif); font-weight:600; font-size:19px; color:var(--ink); margin:0 0 3px; letter-spacing:-.01em;}
.subband .copy p{font-family:var(--serif); font-size:14px; color:var(--muted); margin:0;}
.subband form{display:flex; gap:8px; flex:1 1 300px;}
.subband input{flex:1; font-family:var(--sans); font-size:14px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:10px 13px; min-width:0;}
.subband input::placeholder{color:var(--muted);}
.subband input:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}

@media (max-width:560px){
  .toolbar{flex-direction:column; align-items:stretch;}
  .seg{width:100%;} .seg button{flex:1;}
  .listhead{display:none;}
  li.issue > a{grid-template-columns:1fr; gap:6px; padding:16px 4px;}
  .idx{flex-direction:row; align-items:baseline; gap:8px; order:-1;}
  .idx .date::before{content:none;}
  .rt{flex-direction:row; align-items:center; gap:12px; justify-content:flex-start;}
  .subband{flex-direction:column; align-items:stretch; gap:16px;}
  .subband form{flex-direction:column; flex:0 0 auto;} .subband input{flex:0 0 auto; width:100%;} .subband .btn{width:100%;}
}
`;

export const indexJs = String.raw`(function(){
  var ul=document.getElementById('index'); if(!ul) return;
  var region=document.getElementById('loadmore');
  var statusNode=region?region.querySelector('.loadmore-status'):null;
  var total=parseInt(ul.getAttribute('data-total')||'0',10);
  var year=String(new Date().getFullYear());
  function issues(){ return ul.querySelectorAll('li.issue').length; }
  function announce(m){ if(statusNode) statusNode.textContent=m; }
  function focusFirstNew(li){ if(!li) return; var a=li.querySelector('a'); if(a) a.focus(); } // the row <a> is already tabbable — don't add tabindex=-1 (would drop it from Tab order)
  function swap(fn){ if(document.startViewTransition){ document.startViewTransition(fn); } else { fn(); } }
  // Parse an <li> fragment (valid only inside a list context), splitting off the end sentinel.
  function parse(html){ var t=document.createElement('ul'); t.innerHTML=html;
    var s=t.querySelector('.more-sentinel'); var next=s?s.getAttribute('data-next-before'):null; if(s) s.remove();
    return {frag:t, next:next}; }
  function setLink(cursor){ var a=document.getElementById('loadMore');
    if(!a){ a=document.createElement('a'); a.className='btn secondary'; a.id='loadMore'; a.setAttribute('rel','next');
      a.textContent='Load older issues'; if(region&&statusNode) region.insertBefore(a, statusNode); }
    a.setAttribute('href','/?before='+cursor); }
  function clearLink(){ var a=document.getElementById('loadMore'); if(a) a.remove(); }
  function endState(){ clearLink(); if(!region) return; var last=ul.querySelector('li.issue:last-child'); var s="That’s the whole archive.";
    if(last){ var d=last.querySelector('.date'); var yr=(last.getAttribute('data-date')||'').slice(0,4);
      if(d&&yr) s="That’s the whole archive, back to "+d.textContent+" "+yr+"."; }
    region.innerHTML='<p class="loadmore-end" role="status">'+s+'</p>'; }

  // Load more (append) — delegated so a re-created button still works.
  function loadMore(a){
    if(a.getAttribute('aria-disabled')==='true') return; // re-entrancy guard: aria-disabled doesn't block <a> clicks
    var m=(a.getAttribute('href')||'').match(/before=([0-9-]+)/); if(!m) return; var cursor=m[1];
    a.setAttribute('aria-busy','true'); a.setAttribute('aria-disabled','true'); if(region) region.classList.remove('is-error');
    announce('Loading older issues…');
    fetch('/archive?before='+encodeURIComponent(cursor)+'&limit=30').then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li.issue');
        // Post-append work runs INSIDE swap: View Transitions defer the DOM mutation, so counting/
        // focusing/announcing outside the callback would read the pre-append (stale) state.
        swap(function(){
          while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
          if(pr.next){ setLink(pr.next); announce('Showing '+issues()+' of '+total+' issues'); } else { endState(); }
          focusFirstNew(first);
        }); })
      .catch(function(){ a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
        if(region) region.classList.add('is-error'); announce("Couldn’t load older issues. Tap to retry."); });
  }
  document.addEventListener('click', function(e){ var a=e.target.closest&&e.target.closest('#loadMore'); if(a){ e.preventDefault(); loadMore(a); } });

  // Segment (replace) — each segment is a server scope; switching resets the list.
  function replaceWith(url, keepLoadmore){
    if(region) region.classList.remove('is-error');
    announce('Loading…');
    fetch(url).then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li.issue');
        swap(function(){
          ul.innerHTML=''; while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          if(keepLoadmore && pr.next){ setLink(pr.next); announce('Showing '+issues()+' of '+total+' issues'); }
          else { clearLink(); announce('Showing '+issues()+(keepLoadmore?' of '+total:'')+' issues'); }
          focusFirstNew(first);
        }); })
      .catch(function(){ if(region) region.classList.add('is-error'); announce("Couldn’t load. Try again."); });
  }
  var segBtns=[].slice.call(document.querySelectorAll('.seg button'));
  segBtns.forEach(function(b){ b.addEventListener('click', function(){
    segBtns.forEach(function(x){ x.setAttribute('aria-pressed', x===b?'true':'false'); });
    var mode=b.getAttribute('data-seg');
    if(mode==='all') replaceWith('/archive?limit=30', true);
    else if(mode==='year') replaceWith('/archive?year='+year, false);
    else replaceWith('/archive?limit=15', false);
  }); });

  var dj=document.getElementById('dateJump');
  if(dj) dj.addEventListener('change', function(){ if(dj.value) location.href='/'+dj.value; });
})();`;

export const sourcesCss = String.raw`
.method{font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.6; margin:20px 0 0;}
.method a{text-decoration:none; background-image:linear-gradient(var(--accent-ink),var(--accent-ink)); background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.spectrum{margin:28px 0 8px;}
.spec-counts, .spec-labels{display:grid; grid-template-columns:repeat(7,1fr);}
.spec-counts span{text-align:center; font-family:var(--serif); font-weight:600; font-size:19px; color:var(--ink);
  font-variant-numeric:tabular-nums; line-height:1; padding-bottom:6px;}
.spec-bar2{display:grid; grid-template-columns:repeat(7,1fr); height:36px; border-radius:6px; overflow:hidden; border:1px solid var(--line);}
.spec-bar2 .z{display:block;}
.spec-bar2 .e-l{background:color-mix(in srgb, var(--bias-l) 13%, var(--paper));}
.spec-bar2 .e-r{background:color-mix(in srgb, var(--bias-r) 13%, var(--paper));}
.spec-bar2 .on-l{background:var(--bias-l);} .spec-bar2 .on-c{background:var(--bias-c);} .spec-bar2 .on-r{background:var(--bias-r);}
.spec-labels{margin-top:8px;}
.spec-labels span, .spec-labels a{text-align:center; font-family:var(--mono); font-size:9px; letter-spacing:.06em;
  text-transform:uppercase; color:var(--muted); text-decoration:none; padding:0 2px; line-height:1.3;}
.spec-labels a.on{color:var(--ink2);}
.spec-labels a.on:hover{color:var(--accent-ink);}
.spec-cap{font-family:var(--sans); font-size:12px; color:var(--muted); margin:14px 0 0;}
.sec{margin-top:44px;}
.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}
.sec-h .dot{width:9px; height:9px; border-radius:999px;}
.sec-h .dot.l{background:var(--bias-l);} .sec-h .dot.c{background:var(--bias-c);} .sec-h .dot.r{background:var(--bias-r);}
.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}
.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}
.colhead{display:grid; grid-template-columns:1fr auto; gap:16px; padding:10px 8px 6px;
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);}
.colhead .r{text-align:right;}
ul.srcs{list-style:none; margin:0; padding:0;}
ul.srcs li{border-top:1px solid var(--line);}
ul.srcs li a.row{display:grid; grid-template-columns:1fr auto; gap:16px; align-items:center; padding:13px 8px;
  text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
ul.srcs li a.row:hover{background:var(--wash);}
ul.srcs li a.row:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.nm{display:block; font-family:var(--serif); font-size:17px; font-weight:600; color:var(--ink); letter-spacing:-.004em;}
a.row:hover .nm{color:var(--accent-ink);}
.nm .feeds{font-family:var(--mono); font-size:11px; font-weight:400; color:var(--muted); letter-spacing:0;}
.persp{display:block; font-family:var(--sans); font-size:12.5px; color:var(--muted); margin-top:3px;}
.fact{display:grid; grid-template-columns:14px 70px; align-items:center; gap:9px; white-space:nowrap;}
.meter{display:inline-flex; gap:2px; align-items:flex-end; height:12px;}
.meter i{display:block; width:3px; background:var(--ink2);}
.meter i:nth-child(1){height:6px;} .meter i:nth-child(2){height:9px;} .meter i:nth-child(3){height:12px;}
.meter i.off{background:var(--line-strong);}
.fact .fl{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink2);}
a.row:hover .fact .fl{color:var(--accent-ink);}
`;

export const searchCss = String.raw`
.searchform{display:flex; gap:10px; align-items:center; margin-top:16px;}
.searchfield{flex:1; min-width:0; font-family:var(--sans); font-size:16px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line-strong); border-radius:var(--r-input); padding:11px 14px;}
.searchfield::placeholder{color:var(--muted);}
.searchfield:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}
.searchbtn{font-family:var(--sans); font-size:13px; font-weight:600; color:#fff; background:var(--accent-ink);
  border:1px solid var(--accent-ink); border-radius:var(--r-input); padding:11px 18px; cursor:pointer; white-space:nowrap;}
@media (prefers-color-scheme:dark){ .searchbtn{background:var(--accent); color:#16150f; border-color:var(--accent);} }
:root[data-theme="dark"]  .searchbtn{background:var(--accent); color:#16150f; border-color:var(--accent);}
:root[data-theme="light"] .searchbtn{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
.rescount{font-family:var(--mono); font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin:24px 0 8px;}
.rescount b{color:var(--ink2); font-weight:600;}
.results{list-style:none; margin:0; padding:0;}
.result{border-top:1px solid var(--line);}
.result a{display:grid; grid-template-columns:120px 1fr; gap:4px 18px; align-items:baseline; padding:16px 0; text-decoration:none; border-radius:6px;}
.result a:hover{background:var(--wash);}
.result a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.result .r-date{font-family:var(--mono); font-size:12px; letter-spacing:.04em; color:var(--muted); white-space:nowrap;}
.result .r-tier{grid-column:1; font-family:var(--mono); font-size:10px; letter-spacing:.1em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-top:2px;}
.result .r-tier.must{color:var(--accent-ink);}
.result .r-head{grid-column:2; grid-row:1 / span 2; font-family:var(--serif); font-size:18px; font-weight:600; line-height:1.3; letter-spacing:-.01em; color:var(--ink); text-wrap:pretty;}
.result a:hover .r-head{color:var(--accent-ink);}
.result.unlinked{display:grid; grid-template-columns:120px 1fr; gap:4px 18px; padding:16px 0;}
.note{font-family:var(--sans); font-size:13px; color:var(--muted); margin-top:24px;}
`;

export const statsCss = "\n.toolbar{margin:24px 0 0;}\n.seg{display:inline-flex; gap:2px; background:var(--wash); border:1px solid var(--line); border-radius:var(--r-input); padding:3px;}\n.seg a{font-family:var(--sans); font-size:12px; color:var(--muted); text-decoration:none; padding:7px 15px; border-radius:4px;}\n.seg a:hover{color:var(--ink2);}\n.seg a[aria-current=\"true\"]{background:var(--panel); color:var(--ink); font-weight:600; box-shadow:0 1px 2px rgba(25,25,23,.10);}\n.seg a:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}\nsection{margin-top:44px;}\n.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}\n.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}\n.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}\n.note{font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.6; margin:14px 0 0;}\n.note code{font-family:var(--mono); font-size:12px; color:var(--ink2);}\n.stats{display:flex; flex-wrap:wrap; gap:14px 40px; margin-top:16px;}\n.st .v{font-family:var(--serif); font-weight:600; font-size:27px; color:var(--ink); line-height:1; font-variant-numeric:tabular-nums; letter-spacing:-.01em; display:block;}\n.st .v.ok{color:var(--ok-ink);} .st .v.warn{color:var(--warn-ink);} .st .v.bad{color:var(--accent-ink);}\n.st .l{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin-top:6px; display:block;}\n.st .s{font-family:var(--sans); font-size:12px; color:var(--ink2); margin-top:2px; display:block;}\n/* balance spectrum */\n.bal{margin-top:20px;}\n.balrow{display:grid; grid-template-columns:76px 1fr; gap:14px; align-items:start; margin-bottom:14px;}\n.balrow .rl{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); text-align:right; padding-top:6px;}\n.sbar{display:flex; height:22px; border-radius:5px; overflow:hidden; border:1px solid var(--line);}\n.sbar span{display:block;}\n.sbar .on-l{background:var(--bias-l);} .sbar .on-c{background:var(--bias-c);} .sbar .on-r{background:var(--bias-r);}\n/* each bucket's % sits directly under its portion of the bar (box width == segment width) */\n.bkeys{display:flex; margin-top:5px; font-family:var(--mono); font-size:10px; font-variant-numeric:tabular-nums;}\n.bkey{text-align:center; white-space:nowrap; overflow:hidden; font-weight:600; color:var(--ink2);}\n/* one shared colour-key legend for both bars (redundant colour + name, legible in greyscale) */\n.ballegend{display:flex; gap:20px; font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:10px 0 16px 90px;}\n.ballegend .k{display:inline-flex; align-items:center; gap:7px;}\n.ballegend .sw{width:9px; height:9px; border-radius:2px; flex:none;}\n.ballegend .k-l .sw{background:var(--bias-l);} .ballegend .k-c .sw{background:var(--bias-c);} .ballegend .k-r .sw{background:var(--bias-r);}\n/* concentration share bars */\n.shares{margin-top:16px; display:flex; flex-direction:column; gap:8px;}\n.share{display:grid; grid-template-columns:130px 1fr 42px; gap:12px; align-items:center;}\n.share .sn{font-family:var(--serif); font-size:14px; color:var(--ink2);}\n.share .track{height:8px; background:var(--wash); border-radius:999px; overflow:hidden;}\n/* display:block is load-bearing -- .track is a grid child so it blockifies, but\n   .fill is a plain inline span and silently ignores width/height, so every share\n   bar rendered as an empty track in production. `.sbar span` above has the same fix. */\n.share .fill{display:block; height:100%; background:var(--bias-c); border-radius:999px;}\n.share .pc{font-family:var(--mono); font-size:12px; color:var(--muted); text-align:right; font-variant-numeric:tabular-nums;}\n.drill{font-family:var(--sans); font-size:13px; color:var(--muted); margin-top:14px;}\n.drill code{font-family:var(--mono); font-size:12px; color:var(--ink2);}\n/* tables */\n.tbl-wrap{overflow-x:auto; margin-top:8px;}\ntable{width:100%; border-collapse:collapse; min-width:460px;}\nthead th{font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);\n  font-weight:400; text-align:left; padding:12px 10px 8px; border-bottom:1px solid var(--line-strong); white-space:nowrap;}\nthead th.n{text-align:right;}\ntbody tr:hover{background:var(--wash);}\ntbody td{padding:10px; border-bottom:1px solid var(--line); vertical-align:baseline;}\ntd.src{font-family:var(--serif); font-size:15px; font-weight:600; color:var(--ink);}\ntd.n{font-family:var(--mono); font-size:13px; color:var(--ink2); text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}\ntd.time{font-family:var(--mono); font-size:12.5px; color:var(--ink2); white-space:nowrap; font-variant-numeric:tabular-nums;}\n.rate{display:inline-flex; align-items:center; gap:6px; font-family:var(--mono); font-size:13px; font-variant-numeric:tabular-nums; justify-content:flex-end;}\n.rate .ic{font-family:var(--sans); font-size:11px;}\n.rate.ok{color:var(--ok-ink);} .rate.ok .ic{color:var(--ok);}\n.rate.warn{color:var(--warn-ink);} .rate.warn .ic{color:var(--warn);}\n.rate.bad{color:var(--accent-ink);} .rate.bad .ic{color:var(--accent);}\ntd.rate-cell{text-align:right;}\n@media (max-width:560px){\n  .balrow{grid-template-columns:1fr;} .balrow .rl{text-align:left;}\n  .ballegend{margin-left:0;}\n  .share{grid-template-columns:100px 1fr 40px;}\n}\n";

export const threadsCss = String.raw`
.loadmore{text-align:center; margin:32px 0 0; display:flex; flex-direction:column; align-items:center; gap:10px;}
.loadmore-status{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0; min-height:1em;}
.loadmore.is-error .loadmore-status{color:var(--accent-ink);}
.loadmore-end{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0;}
section{margin-top:40px;}
.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}
.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}
.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}
ul.threads{list-style:none; margin:0; padding:0;}
ul.threads li{border-top:1px solid var(--line);}
ul.threads li:first-child{border-top:none;}
ul.threads li a{display:grid; grid-template-columns:auto 1fr auto; gap:14px; align-items:baseline; padding:15px 8px;
  text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
ul.threads li a:hover{background:var(--wash);}
ul.threads li a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.mk{width:9px; height:9px; margin-top:7px;}
.mk.on{background:var(--accent); border-radius:999px;}
.mk.dorm{border:1.5px solid var(--muted); border-radius:999px; background:transparent;}
.tl .lbl{font-family:var(--serif); font-size:18px; font-weight:600; color:var(--ink); line-height:1.3; letter-spacing:-.006em; display:block; text-wrap:pretty;}
ul.threads li a:hover .lbl{color:var(--accent-ink);}
.tl .last{font-family:var(--serif); font-size:14px; color:var(--muted); margin-top:3px; display:block; line-height:1.45; text-wrap:pretty;}
.meta{text-align:right; white-space:nowrap; display:flex; flex-direction:column; gap:4px; align-items:flex-end;}
.meta .st{font-family:var(--mono); font-size:10px; letter-spacing:.07em; text-transform:uppercase;}
.meta .st.on{color:var(--accent-ink);} .meta .st.dorm{color:var(--muted);}
.meta .upd{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
.empty{font-family:var(--serif); font-size:18px; color:var(--muted); margin-top:28px;}
@media (max-width:560px){
  ul.threads li a{grid-template-columns:auto 1fr; gap:12px;}
  .meta{grid-column:2; flex-direction:row; align-items:baseline; gap:10px; margin-top:2px;}
}
`;

export const threadsJs = String.raw`(function(){
  var ul=document.getElementById('older-threads'); if(!ul) return;
  var region=document.getElementById('loadmore');
  var statusNode=region?region.querySelector('.loadmore-status'):null;
  var total=parseInt(ul.getAttribute('data-total')||'0',10);
  function rows(){ return ul.querySelectorAll('li').length; }
  function announce(m){ if(statusNode) statusNode.textContent=m; }
  function swap(fn){ if(document.startViewTransition){ document.startViewTransition(fn); } else { fn(); } }
  function parse(html){ var t=document.createElement('ul'); t.innerHTML=html;
    var s=t.querySelector('.more-sentinel');
    var next=s?{ts:s.getAttribute('data-next-before'), id:s.getAttribute('data-next-id')}:null;
    if(s) s.remove();
    return {frag:t, next:next}; }
  function href(n){ return '/threads?before='+encodeURIComponent(n.ts)+'&before_id='+encodeURIComponent(n.id); }
  function setLink(n){ var a=document.getElementById('loadMore'); if(a) a.setAttribute('href', href(n)); }
  function endState(){ var a=document.getElementById('loadMore'); if(a) a.remove();
    if(region) region.innerHTML='<p class="loadmore-end" role="status">That’s every thread.</p>'; }
  function loadMore(a){
    if(a.getAttribute('aria-disabled')==='true') return; // aria-disabled doesn't block <a> clicks
    var h=a.getAttribute('href')||''; var qs=h.slice(h.indexOf('?')+1);
    a.setAttribute('aria-busy','true'); a.setAttribute('aria-disabled','true');
    if(region) region.classList.remove('is-error');
    announce('Loading older threads…');
    fetch('/threads/more?'+qs).then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li');
        // Inside swap(): View Transitions defer the mutation, so counting/focusing outside it
        // would read the pre-append state.
        swap(function(){
          while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
          if(pr.next){ setLink(pr.next); announce('Showing '+rows()+' of '+total+' earlier threads'); }
          else { endState(); }
          if(first){ var link=first.querySelector('a'); if(link) link.focus(); }
        }); })
      .catch(function(){ a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
        if(region) region.classList.add('is-error'); announce('Couldn’t load older threads. Tap to retry.'); });
  }
  document.addEventListener('click', function(e){ var a=e.target.closest&&e.target.closest('#loadMore'); if(a){ e.preventDefault(); loadMore(a); } });
})();`;

export const threadCss = String.raw`
.mast{border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:24px;}
.brandline{font-family:var(--serif); font-size:15px; color:var(--muted); margin:0 0 14px;}
.brandline em{color:var(--accent-ink); font-style:normal; font-weight:600;}
.statusrow{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:0 0 10px;}
.dot{width:9px; height:9px; border-radius:50%; flex:none;}
.dot.on{background:var(--accent);}
.dot.dorm{background:none; border:1.5px solid var(--muted);}
.status{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink);}
.status.off{color:var(--muted);}
.span{font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-variant-numeric:tabular-nums;}
.span .divider{color:var(--line-strong); padding:0 2px;}
h1{font-family:var(--serif); font-weight:600; font-size:30px; line-height:1.15; letter-spacing:-.01em; margin:0 0 16px; text-wrap:pretty;}
@media (min-width:760px){ h1{font-size:34px;} }
.sofar{font-family:var(--serif); font-size:18px; line-height:1.55; color:var(--ink2); margin:0; text-wrap:pretty;}
.sofar .lead{font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-right:8px;}
/* An editorial block, not an alert box: a full-width hairline rule + mono label, matching the
   page's ruled-section language. The label keeps the lone accent as a live-tracking signal. */
.ledger{border-top:1px solid var(--line); padding:24px 0 0; margin:0 0 44px;}
.ledger-label{display:flex; align-items:center; gap:8px; font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink); margin:0 0 16px;}
.ledger-label::before{content:""; width:6px; height:6px; border-radius:50%; background:var(--accent); flex:none;}
.qlist{list-style:none; margin:0; padding:0;}
.qlist li{position:relative; padding:0 0 0 20px; margin:0 0 12px; color:var(--ink2); font-size:16px; line-height:1.5; text-wrap:pretty;}
.qlist li:last-child{margin-bottom:0;}
.qlist li::before{content:""; position:absolute; left:2px; top:.62em; width:6px; height:6px; border-radius:50%; border:1.5px solid var(--muted);}
.tl-label{font-family:var(--sans); font-size:11px; font-weight:700; letter-spacing:.18em; text-transform:uppercase; color:var(--ink); margin:0 0 24px; padding-bottom:8px; border-bottom:1px solid var(--line);}
.timeline{list-style:none; margin:0; padding:0; position:relative;}
.update{position:relative; padding:0 0 34px 30px;}
.update::before{content:""; position:absolute; left:0; top:5px; width:9px; height:9px; border-radius:50%; background:var(--muted); z-index:1;}
.update.quiet::before{background:none; border:1.5px solid var(--line-strong);}
.update:not(:last-child)::after{content:""; position:absolute; left:4px; top:12px; bottom:-2px; width:1px; background:var(--line);}
.when{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0 0 6px; font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted);}
.when .date{color:var(--ink2); font-weight:600;}
.when .issue{font-family:var(--mono); font-size:11px; color:var(--accent-ink); text-decoration:none;}
.when .issue:hover{text-decoration:underline;}
.uhead{font-family:var(--serif); font-weight:600; font-size:20px; line-height:1.28; letter-spacing:-.01em; margin:0 0 12px; text-wrap:pretty;}
.update.quiet .uhead{font-size:16px; color:var(--muted); font-weight:400; font-style:italic;}
.facts{list-style:none; margin:0; padding:0;}
.facts>li{position:relative; padding:0 0 0 18px; margin:0 0 12px; color:var(--ink2); line-height:1.55; text-wrap:pretty;}
.facts>li::before{content:""; position:absolute; left:0; top:.68em; width:5px; height:5px; background:var(--line-strong);}
.backlink{font-family:var(--sans); font-size:13px; margin-top:8px;}
.backlink a{color:var(--muted); text-decoration:none;} .backlink a:hover{color:var(--accent-ink);}
`;

export const askCss = String.raw`
.narrow{max-width:680px;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:20px 0 0;}

/* The ask area is ONE control group in a recessed panel, on the same tokens as the subscribe
   band on the index. Three different jobs -- type here, send this, try an example -- have to
   look like three different things, or the page reads as a row of identical rules and nothing
   tells a reader where to start. */
.askbox{margin:28px 0 0; padding:18px 20px 20px; background:var(--panel-2);
  border:1px solid var(--line); border-radius:var(--r-card);}
.asklabel{display:block; font-family:var(--sans); font-size:11px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); margin:0 0 10px;}
.askform{display:flex; gap:10px; align-items:flex-end;}
.askin{flex:1; font-family:var(--serif); font-size:16px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:11px 13px; resize:none;
  line-height:1.5; min-width:0;}
.askin::placeholder{color:var(--muted);}
.askin:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}
.asksend{font-family:var(--sans); font-size:14px; font-weight:600; color:var(--panel);
  background:var(--ink); border:1px solid var(--ink); border-radius:var(--r-input);
  padding:11px 20px; cursor:pointer; white-space:nowrap;}
.asksend:hover:not(:disabled){background:var(--accent-ink); border-color:var(--accent-ink);}
.asksend:disabled{opacity:.45; cursor:default;}

/* Examples are pills, not rules: clearly something you press, clearly not the field. */
.chips{display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 0;}
.chip{font-family:var(--sans); font-size:13px; color:var(--ink2); background:var(--panel);
  border:1px solid var(--line); border-radius:999px; padding:6px 14px; cursor:pointer;
  text-align:left;}
.chip:hover{border-color:var(--accent); color:var(--accent-ink);}

/* The conversation. A question is a quiet label over its answer, not another bordered box. */
.thread{margin:28px 0 0; display:flex; flex-direction:column; gap:28px;}
.turn.q{color:var(--ink); font-family:var(--sans); font-size:14px; font-weight:600;
  padding-bottom:10px; border-bottom:1px solid var(--line);}
.turn.a{color:var(--ink2); font-family:var(--serif); font-size:16px; line-height:1.65;}
.turn.a a{color:var(--accent-ink); text-decoration:none;
  background-image:linear-gradient(var(--accent-ink),var(--accent-ink));
  background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.turn.err{color:var(--accent-ink); font-family:var(--serif); font-size:15px;}
.ap{margin:0 0 12px;}
.ap:last-child,.al:last-child{margin-bottom:0;}
.ah{font-family:var(--sans); font-weight:700; font-size:12px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--ink); margin:18px 0 8px;}
.al{margin:0 0 12px; padding-left:20px;}
.al li{margin:0 0 7px; line-height:1.6;}
.steps{display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px;}
.step{font-family:var(--sans); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); background:var(--wash); border-radius:999px; padding:3px 10px;}

.fine{font-family:var(--serif); font-size:13.5px; color:var(--muted); line-height:1.6; margin:28px 0 0;
  border-top:1px solid var(--line); padding-top:16px;}
.fine code{font-family:var(--mono,ui-monospace,Menlo,monospace); font-size:12.5px;}
.off{border:1px solid var(--line); border-radius:var(--r-card); background:var(--panel-2);
  padding:16px 18px; margin:26px 0 0; font-family:var(--serif); font-size:15px; color:var(--muted);}
@media (max-width:560px){ .askform{flex-direction:column; align-items:stretch;} }
`;

export const askJs = String.raw`(function(){
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
})();`;

export const connectCss = String.raw`
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
`;

export const copyJs = String.raw`(function(){
if(!navigator.clipboard)return;
document.querySelectorAll('.copy').forEach(function(b){
  b.hidden=false;
  b.addEventListener('click',function(){
    var pre=document.getElementById(b.getAttribute('data-for'));
    if(!pre)return;
    var t=b.textContent;
    navigator.clipboard.writeText(pre.textContent).then(function(){
      b.textContent='Copied';setTimeout(function(){b.textContent=t;},1400);
    }).catch(function(){
      // Denied permission or an unfocused document: say so rather than leaving a button
      // that silently does nothing. The command is selectable text either way.
      b.textContent='Copy failed';setTimeout(function(){b.textContent=t;},2200);
    });
  });
});
})();`;

export const feedbackCss = String.raw`
.narrow{max-width:620px;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:20px 0 0;}
.body{font-family:var(--serif); font-size:16px; color:var(--muted); line-height:1.65; margin:16px 0 0;}
.cta{display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:28px;}
.cta .btn{font-size:14px; padding:12px 22px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;}
.cta .alt{font-family:var(--serif); font-size:14px; color:var(--muted);}
.cta .alt a{text-decoration:none; background-image:linear-gradient(var(--accent-ink),var(--accent-ink));
  background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.helps{margin-top:36px; border-top:1px solid var(--line); padding-top:20px;}
.helps h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink); margin:0 0 12px;}
.helps ul{margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:9px;}
.helps li{font-family:var(--serif); font-size:15px; color:var(--ink2); line-height:1.5; padding-left:18px; position:relative;}
.helps li::before{content:"—"; position:absolute; left:0; color:var(--accent-ink);}
@media (max-width:560px){ .cta .btn{width:100%; justify-content:center;} }
`;

export const notFoundCss = String.raw`
.narrow{max-width:560px;}
.nf-code{font-family:var(--sans); font-weight:700; font-size:13px; letter-spacing:.18em;
  text-transform:uppercase; color:var(--accent-ink); margin:24px 0 0;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:14px 0 0;}
.body{font-family:var(--serif); font-size:16px; color:var(--muted); line-height:1.65; margin:14px 0 0;}
.ways{margin-top:30px; display:flex; gap:12px; flex-wrap:wrap;}
.ways a{font-family:var(--sans); font-size:14px; text-decoration:none; display:inline-flex;
  align-items:center; gap:8px; border:1px solid var(--line); border-radius:8px; padding:11px 18px;
  color:var(--ink2); transition:border-color .15s ease,color .15s ease;}
.ways a:hover{border-color:var(--accent); color:var(--accent-ink);}
.ways a.primary{border-color:var(--accent); color:var(--accent-ink);}
.ways a:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
@media (max-width:560px){ .ways a{width:100%; justify-content:center;} }
`;

export const skipLinkCss = String.raw`
.skip-link {
    position: absolute;
    left: -9999px;
    top: auto;
    width: 1px;
    height: 1px;
    overflow: hidden;
    z-index: 1000;
    padding: 0.75rem 1.5rem;
    background: var(--bg, #fafaf8);
    color: var(--ruby, #c45a3b);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 0.9rem;
    text-decoration: none;
    border: 2px solid var(--ruby, #c45a3b);
    border-radius: 4px;
}
.skip-link:focus-visible {
    position: fixed;
    left: 1rem;
    top: 1rem;
    width: auto;
    height: auto;
    overflow: visible;
}`;

export const reducedMotionCss = String.raw`
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
    }
    html { scroll-behavior: auto; }
}`;

export const digestNavCss = "\n/* pull the paper's top padding in now that a utility bar sits above the masthead */\n.paper{padding-top:28px;}\n\n/* top utility bar */\n.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:30px;}\n.topnav{font-family:var(--sans);font-size:13px;line-height:normal;display:flex;align-items:center;flex-wrap:nowrap;}\n.topnav a{color:var(--muted);text-decoration:none;}\n.topnav a:hover{color:var(--accent-ink);}\n.topnav a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}\n.topnav .sep{color:var(--hair);padding:0 10px;}\n.topright{display:flex;align-items:center;gap:12px;}\n\n/* translate pill -- a quiet accent-outlined chip (matches the chrome surfaces' `.pill`) */\n.pill{display:inline-flex;align-items:center;gap:6px;font-family:var(--sans);font-size:12px;\n  color:var(--accent-ink);text-decoration:none;line-height:normal;\n  border:1px solid var(--accent);border-radius:999px;padding:5px 12px;\n  transition:background .15s ease,border-color .15s ease;}\n.pill .g{font-family:var(--serif);}\n.pill:hover{background:color-mix(in srgb,var(--accent) 12%,var(--bg));}\n.pill:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}\n\n/* theme toggle -- shared TOGGLE_BTN markup: two stacked glyphs crossfade on hover\n   (current -> next-state), a visual echo of its \"Switch to {next}\" title. */\n.toggle{font-family:var(--sans);font-size:12px;color:var(--muted);background:none;\n  border:1px solid var(--hair);border-radius:6px;padding:6px 10px;cursor:pointer;line-height:normal;\n  display:inline-flex;align-items:center;gap:6px;min-height:24px;}\n.toggle:hover{color:var(--accent-ink);border-color:var(--accent);}\n.toggle:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}\n.tglyphs{position:relative;display:inline-flex;flex:none;width:1em;height:1em;font-size:13px;line-height:1;}\n.tglyph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;line-height:1;\n  transition:opacity .2s ease,transform .2s ease;}\n.tg-next{opacity:0;transform:translateY(35%);}\n.toggle:hover .tg-cur{opacity:0;transform:translateY(-35%);}\n.toggle:hover .tg-next{opacity:1;transform:translateY(0);}\n\n/* Hide the pill once the reader is on Google's translate.goog proxy -- redundant\n   there (Google's own language bar is the backstop). Two signals: Google's own\n   translated-* class on <html>, and the via-proxy hostname flag (head script). */\nhtml.translated-ltr .pill,\nhtml.translated-rtl .pill,\n.via-proxy .pill {\n    display: none;\n}\n\n/* Feedback invitation -- its own line, clearly clickable, so it doesn't read as\n   just another footer link. */\n.footer-feedback {\n    margin: 12px 0 4px;\n    color: var(--muted);\n}\n.footer-feedback a {\n    color: var(--accent-ink);\n    font-weight: 600;\n    text-decoration: underline;\n    text-underline-offset: 2px;\n}\n\n@media (max-width:420px){.tword{display:none;}.toggle{padding:6px 9px;}}\n";

export const proxyTranslateHideScript = String.raw`if(location.hostname.indexOf("translate.goog")>-1)document.documentElement.className+=" via-proxy";`;

