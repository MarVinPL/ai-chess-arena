//! Trusted-source gate for Android GGUF downloads.
//!
//! Inputs must originate from Arena's approved model registry, not JavaScript.
//! This module does not start a transfer or permit arbitrary URLs.
use reqwest::Url;
use crate::android_model_cache::{valid_digest, MAX_MODEL_BYTES};

#[derive(Clone, Debug)]
pub(crate) struct ApprovedGguf {
    pub(crate) sha256: String,
    pub(crate) size_bytes: u64,
    pub(crate) source_url: String,
}

fn safe_component(raw: &str) -> bool {
    !raw.is_empty() && raw.len() <= 128
        && raw.bytes().all(|b| b.is_ascii_alphanumeric()
            || matches!(b, b'-' | b'_' | b'.'))
        && raw != "." && raw != ".."
        && !raw.starts_with('.')
}

/// Hugging Face repository names and exact revisions must be provided by
/// the trusted registry; only an exact model file URL is permitted.
/// Reject credentials, ports, query strings, fragments, encoded slashes and
/// extra path segments. Redirects must be inspected separately by downloader.
pub(crate) fn validate_model_source(source: &ApprovedGguf) -> Result<Url, String> {
    if !valid_digest(&source.sha256) || !(4..=MAX_MODEL_BYTES).contains(&source.size_bytes) {
        return Err("Niezaufane metadane GGUF".into());
    }
    let url = Url::parse(&source.source_url)
        .map_err(|_| "Nieprawidłowy adres pobierania GGUF")?;
    if url.scheme() != "https" || url.host_str() != Some("huggingface.co")
        || url.port().is_some() || !url.username().is_empty()
        || url.password().is_some() || url.query().is_some()
        || url.fragment().is_some() {
        return Err("Niezatwierdzone źródło GGUF".into());
    }
    let segments: Vec<_> = url.path_segments()
        .ok_or("Nieprawidłowa ścieżka GGUF")?.collect();
    // /{owner}/{repo}/resolve/{exact-revision}/{filename.gguf}
    if segments.len() != 5 || segments[2] != "resolve"
        || !segments[..4].iter().all(|s| safe_component(s))
        || !safe_component(segments[4])
        || !segments[4].ends_with(".gguf")
        || segments[4].len() <= 5 {
        return Err("Niezatwierdzona ścieżka GGUF".into());
    }
    // A single reproducible object per registry entry. Branch names such as
    // main would be mutable and must not be treated as a pinned revision.
    if segments[3].len() != 40
        || !segments[3].bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Wymagana dokładna rewizja modelu".into());
    }
    Ok(url)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn candidate(url: &str) -> ApprovedGguf {
        ApprovedGguf {
            sha256: "a".repeat(64),
            size_bytes: 4096,
            source_url: url.into(),
        }
    }
    fn source() -> String {
        format!("https://huggingface.co/owner/repo/resolve/{}/model.Q4_K_M.gguf",
            "1".repeat(40))
    }

    #[test]
    fn pinned_hf_repo_gguf_is_accepted() {
        assert!(validate_model_source(&candidate(&source())).is_ok());
    }

    #[test]
    fn rejects_cross_origin_unpinned_or_credential_urls() {
        let correct = source();
        for bad in [
            correct.replace("https://", "http://"),
            correct.replace("huggingface.co", "huggingface.co.evil.test"),
            correct.replace("/resolve/", "/blob/"),
            correct.replace(&"1".repeat(40), "main"),
            correct.replace(".gguf", ".bin"),
            correct.replace("owner/repo", "owner/repo/other"),
            format!("{correct}?download=true"),
            format!("{correct}#bad"),
            correct.replace("https://", "https://user:password@"),
            correct.replace("huggingface.co/", "huggingface.co:8443/"),
            correct.replace("model.", "model%2F."),
        ] {
            assert!(validate_model_source(&candidate(&bad)).is_err(), "{bad}");
        }
    }

    #[test]
    fn rejects_unverified_digest_and_size() {
        let mut c = candidate(&source());
        c.sha256 = "A".repeat(64);
        assert!(validate_model_source(&c).is_err());
        c.sha256 = "a".repeat(64);
        c.size_bytes = MAX_MODEL_BYTES + 1;
        assert!(validate_model_source(&c).is_err());
    }
}
