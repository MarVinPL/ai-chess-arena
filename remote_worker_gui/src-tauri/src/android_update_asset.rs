//! Candidate metadata gate for a future native Android APK updater.
//! This module NEVER downloads or installs APKs. Metadata is untrusted until
//! downloaded bytes, Android signing certificate, package and versionCode are
//! checked. No network endpoint or installation permission is exposed here.

use reqwest::Url;
use serde_json::Value;

const MAX_APK_BYTES: u64 = 200 * 1024 * 1024;
const MIN_APK_BYTES: u64 = 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ApkCandidate {
    pub(crate) name: String,
    pub(crate) download_url: String,
    pub(crate) size: u64,
}

fn safe_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 128
        && name.is_ascii()
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        && !name.starts_with('.')
        && name.to_ascii_lowercase().ends_with(".apk")
        && {
            let lower = name.to_ascii_lowercase();
            lower.contains("worker")
                && (lower.contains("arm64") || lower.contains("aarch64"))
        }
}

fn exact_release_download_url(raw: &str, tag: &str, filename: &str) -> bool {
    let Ok(url) = Url::parse(raw) else { return false };
    url.scheme() == "https"
        && url.host_str() == Some("github.com")
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && url.query().is_none()
        && url.fragment().is_none()
        && url.path() == format!(
            "/MarVinPL/ai-chess-arena/releases/download/{tag}/{filename}"
        )
}

/// Fail closed: zero or multiple eligible ARM64 APKs are ambiguous.
/// A validated candidate is NOT permission to install.
pub(crate) fn select_apk_candidate(release: &Value, expected_tag: &str)
    -> Option<ApkCandidate> {
    if !expected_tag.starts_with("worker-v") ||
        expected_tag.len() > 100 ||
        !expected_tag.bytes().all(|c|
            c.is_ascii_alphanumeric() || b"._-".contains(&c)) {
        return None;
    }
    if release.get("tag_name")?.as_str()? != expected_tag
        || release.get("draft")?.as_bool()? 
        || release.get("assets")?.as_array()?.len() > 100 {
        return None;
    }
    let mut candidates = release.get("assets")?.as_array()?.iter().filter_map(|asset| {
        let name = asset.get("name")?.as_str()?;
        if !safe_name(name) { return None; }
        let size = asset.get("size")?.as_u64()?;
        if !(MIN_APK_BYTES..=MAX_APK_BYTES).contains(&size) { return None; }
        // GitHub must mark an asset as uploaded, not a partially staged draft.
        if asset.get("state")?.as_str()? != "uploaded" { return None; }
        let raw = asset.get("browser_download_url")?.as_str()?;
        if !exact_release_download_url(raw, expected_tag, name) { return None; }
        Some(ApkCandidate {
            name: name.to_owned(),
            download_url: raw.to_owned(),
            size,
        })
    });
    let candidate = candidates.next()?;
    if candidates.next().is_some() { return None; }
    Some(candidate)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn release() -> Value {
        json!({
            "tag_name": "worker-v1.2.3",
            "draft": false,
            "prerelease": false,
            "assets": [{
                "name": "ai-chess-arena-worker-aarch64.apk",
                "size": 2097152,
                "state": "uploaded",
                "browser_download_url":
                    "https://github.com/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk"
            }]
        })
    }

    #[test]
    fn identifies_one_exact_official_arm64_apk() {
        let selected = select_apk_candidate(&release(), "worker-v1.2.3").unwrap();
        assert_eq!(selected.size, 2097152);
        assert_eq!(selected.name, "ai-chess-arena-worker-aarch64.apk");
    }

    #[test]
    fn rejects_wrong_tags_urls_and_insecure_hosts() {
        assert!(select_apk_candidate(&release(), "worker-v1.2.4").is_none());
        for url in [
            "http://github.com/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk",
            "https://github.com.evil.test/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk",
            "https://github.com/Other/repo/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk",
            "https://github.com/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk?token=x",
            "https://github.com/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk#fragment",
        ] {
            let mut data = release();
            data["assets"][0]["browser_download_url"] = json!(url);
            assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none(), "{url}");
        }
    }

    #[test]
    fn rejects_ambiguous_missing_or_outsize_assets() {
        let mut data = release();
        let duplicate = data["assets"][0].clone();
        data["assets"].as_array_mut().unwrap().push(duplicate);
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        let mut data = release();
        data["assets"] = json!([]);
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        for size in [0, 1023, MAX_APK_BYTES + 1] {
            let mut data = release();
            data["assets"][0]["size"] = json!(size);
            assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        }
    }

    #[test]
    fn rejects_wrong_platform_pending_and_draft() {
        for name in ["worker-x86.apk", "../worker-aarch64.apk",
            "worker-aarch64.zip", "worker-aarch64.apk/evil"] {
            let mut data = release();
            data["assets"][0]["name"] = json!(name);
            assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        }
        let mut data = release();
        data["assets"][0]["state"] = json!("new");
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        let mut data = release();
        data["draft"] = json!(true);
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
    }
}
