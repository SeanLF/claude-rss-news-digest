//! HTML templates for the digest server.

mod chrome;
mod digest;
mod feedback;
mod index;
mod search;
mod sources;
mod stats;
mod thread;

pub use chrome::{TOGGLE_BTN, footer as chrome_footer, topbar as chrome_topbar};
pub use digest::{
    DIGEST_NAV_CSS, FAVICON_SVG, PROXY_TRANSLATE_HIDE_SCRIPT, REDUCED_MOTION_CSS, SKIP_LINK_CSS,
    SKIP_LINK_HTML, digest_nav_html, digest_og_tags, web_footer_html,
};
pub use feedback::{FeedbackParams, render_feedback};
pub use index::{IndexParams, render_index};
pub use search::{SearchParams, render_search};
pub use sources::{Source, SourcesParams, render_sources};
pub use stats::render_stats;
pub use thread::{ThreadParams, ThreadsIndexParams, render_thread, render_threads_index};
