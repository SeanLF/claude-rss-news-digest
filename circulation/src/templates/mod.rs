//! HTML templates for the digest server.

mod digest;
mod index;
mod sources;
mod stats;
mod thread;

pub use digest::{
    DIGEST_NAV_CSS, DIGEST_NAV_HTML, FAVICON_SVG, REDUCED_MOTION_CSS, SKIP_LINK_CSS,
    SKIP_LINK_HTML, digest_og_tags, web_footer_html,
};
pub use index::{IndexParams, render_index};
pub use sources::{Source, render_sources};
pub use stats::render_stats;
pub use thread::{render_thread, render_threads_index};
