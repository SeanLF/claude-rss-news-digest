//! HTML templates for the digest server.

mod digest;
mod index;
mod stats;

pub use digest::{DIGEST_NAV_CSS, DIGEST_NAV_HTML, FAVICON_SVG, digest_og_tags, web_footer_html};
pub use index::render_index;
pub use stats::render_stats;
