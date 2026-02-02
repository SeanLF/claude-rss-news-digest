//! HTML templates for the digest server.

mod digest;
mod index;
mod stats;

pub use digest::{DIGEST_NAV_CSS, DIGEST_NAV_HTML};
pub use index::render_index;
pub use stats::render_stats;
