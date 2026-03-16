//! HTML templates for the digest server.

mod digest;
mod index;
mod sources;
mod stats;

pub use digest::{DIGEST_NAV_CSS, DIGEST_NAV_HTML, FAVICON_SVG, digest_og_tags, web_footer_html};
pub use index::{IndexParams, render_index};
pub use sources::{Source, render_sources};
pub use stats::render_stats;
