//! Candidate metadata gate for a future native Android APK updater.
//! This module NEVER downloads or installs APKs. Metadata is untrusted until
//! downloaded bytes, Android signing certificate, package and versionCode are
//! checked. No network endpoint or installation permission is exposed here.

use reqwest::Url;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::{self, Read};

const MAX_APK_BYTES: u64 = 200 * 1024 * 1024;
const MIN_APK_BYTES: u64 = 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ApkCandidate {
    pub(crate) name: String,
    pub(crate) download_url: String,
    pub(crate) size: u64,
    pub(crate) expected_sha256: String,
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
    // Stable Worker tags only: a malformed tag or prerelease can never
    // become an Android installer candidate, even if its URL looks official.
    let version = expected_tag.strip_prefix("worker-v")?;
    crate::numeric_version(version)?;
    if release.get("tag_name")?.as_str()? != expected_tag
        || release.get("draft")?.as_bool()?
        || release.get("prerelease")?.as_bool()?
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
        // GitHub release asset digest only detects transfer corruption; it
        // does NOT replace Android signing certificate verification.
        let digest = asset.get("digest")?.as_str()?.strip_prefix("sha256:")?;
        if digest.len() != 64 || !digest.bytes().all(|c|
            c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
            return None;
        }
        let raw = asset.get("browser_download_url")?.as_str()?;
        if !exact_release_download_url(raw, expected_tag, name) { return None; }
        Some(ApkCandidate {
            name: name.to_owned(),
            download_url: raw.to_owned(),
            size,
            expected_sha256: digest.to_owned(),
        })
    });
    let candidate = candidates.next()?;
    if candidates.next().is_some() { return None; }
    Some(candidate)
}


/// Verify already-opened, privately staged APK bytes without buffering the
/// whole file. This is only an integrity check, NOT APK certificate validation
/// or authorization to install. The future Android staging layer must open a
/// private regular file and independently verify its signature/version.
pub(crate) fn verify_apk_stream<R: Read>(
    reader: &mut R,
    candidate: &ApkCandidate,
) -> io::Result<bool> {
    if !(MIN_APK_BYTES..=MAX_APK_BYTES).contains(&candidate.size)
        || candidate.expected_sha256.len() != 64
        || !candidate.expected_sha256.bytes().all(|c|
            c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Ok(false);
    }

    let mut sha = Sha256::new();
    let mut bytes_read = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        // Read up to the remaining declared size + one byte, so an oversized
        // response is rejected before hashing more than the allowed budget.
        let capacity = buffer.len().min(
            (candidate.size - bytes_read).saturating_add(1) as usize,
        );
        let count = reader.read(&mut buffer[..capacity])?;
        if count == 0 { break; }
        bytes_read += count as u64;
        if bytes_read > candidate.size { return Ok(false); }
        sha.update(&buffer[..count]);
    }
    if bytes_read != candidate.size { return Ok(false); }
    let digest = sha.finalize();
    let actual = format!("{digest:x}");
    Ok(actual == candidate.expected_sha256)
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
                "digest": format!("sha256:{}", "a".repeat(64)),
                "browser_download_url":
                    "https://github.com/MarVinPL/ai-chess-arena/releases/download/worker-v1.2.3/ai-chess-arena-worker-aarch64.apk"
            }]
        })
    }

    #[test]
    fn streaming_digest_checks_exact_size_and_bytes() {
        use std::io::Cursor;
        let payload = vec![b'W'; 2048];
        let expected = format!("{:x}", Sha256::digest(&payload));
        let mut candidate = select_apk_candidate(&release(), "worker-v1.2.3").unwrap();
        candidate.size = payload.len() as u64;
        candidate.expected_sha256 = expected;
        assert!(verify_apk_stream(&mut Cursor::new(&payload), &candidate).unwrap());

        // Same length but corrupted bytes must fail.
        let mut corrupt = payload.clone();
        corrupt[150] ^= 0x01;
        assert!(!verify_apk_stream(&mut Cursor::new(&corrupt), &candidate).unwrap());

        // Truncated and oversized downloads are rejected independently of hash.
        assert!(!verify_apk_stream(&mut Cursor::new(&payload[..2047]), &candidate).unwrap());
        let mut oversized = payload.clone();
        oversized.push(b'X');
        assert!(!verify_apk_stream(&mut Cursor::new(&oversized), &candidate).unwrap());
        candidate.expected_sha256 = "a".repeat(64);
        assert!(!verify_apk_stream(&mut Cursor::new(&payload), &candidate).unwrap());
    }

    #[test]
    fn streaming_digest_fails_closed_for_bad_metadata_and_read_errors() {
        use std::io::{Cursor, Error, ErrorKind};
        struct FailRead;
        impl Read for FailRead {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(Error::new(ErrorKind::Interrupted, "simulated failure"))
            }
        }
        let mut candidate = select_apk_candidate(&release(), "worker-v1.2.3").unwrap();
        assert!(verify_apk_stream(&mut FailRead, &candidate).is_err());
        candidate.size = MAX_APK_BYTES + 1;
        assert!(!verify_apk_stream(&mut Cursor::new(vec![0u8; 16]), &candidate).unwrap());
        candidate.size = MIN_APK_BYTES;
        candidate.expected_sha256 = "bad".to_owned();
        assert!(!verify_apk_stream(&mut Cursor::new(vec![0u8; 1024]), &candidate).unwrap());
    }

    #[test]
    fn identifies_one_exact_official_arm64_apk() {
        let selected = select_apk_candidate(&release(), "worker-v1.2.3").unwrap();
        assert_eq!(selected.size, 2097152);
        assert_eq!(selected.expected_sha256, "a".repeat(64));
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
    fn rejects_missing_or_malformed_sha256_digest() {
        for digest in ["", "sha512:xyz", "sha256:bad",
            "sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"] {
            let mut data = release();
            data["assets"][0]["digest"] = json!(digest);
            assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        }
        let mut data = release();
        data["assets"][0].as_object_mut().unwrap().remove("digest");
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
    }

    #[test]
    fn rejects_prereleases_missing_flags_and_malformed_worker_tags() {
        for tag in ["worker-v1.2", "worker-v1.2.3-rc1", "worker-v1.2.3+meta",
                    "worker-v1.2.3/evil", "server-v1.2.3"] {
            assert!(select_apk_candidate(&release(), tag).is_none(), "{tag}");
        }
        for key in ["draft", "prerelease"] {
            let mut data = release();
            data.as_object_mut().unwrap().remove(key);
            assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
        }
        let mut data = release();
        data["prerelease"] = json!(true);
        assert!(select_apk_candidate(&data, "worker-v1.2.3").is_none());
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
