//! Local Android APK identity policy. No Javascript inputs, no installer.
//!
//! The Android platform bridge MUST obtain both records from PackageManager:
//! - installed app: getPackageInfo(packageName, GET_SIGNING_CERTIFICATES)
//! - staged APK: getPackageArchiveInfo(path, GET_SIGNING_CERTIFICATES)
//! - getLongVersionCode() for both.
//!
//! IMPORTANT: compare the actual *current* signing certificates from
//! SigningInfo.getApkContentsSigners(), not manifest text, release metadata,
//! the untrusted APK file name, or a server-provided fingerprint.
//! Fail closed for missing/ambiguous signers and key rotation until the
//! Android platform implementation can validate rotation independently.

pub(crate) const WORKER_PACKAGE_ID: &str = "pl.marvin.aichessarena.worker";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PlatformApkIdentity {
    pub(crate) package_name: String,
    pub(crate) long_version_code: u64,
    /// SHA-256 fingerprint of the sole DER-encoded current signing certificate.
    pub(crate) certificate_sha256: String,
    /// True only if Android PackageManager obtained and verified signing data.
    pub(crate) platform_signing_verified: bool,
    /// Number of current APK signers reported by the OS.
    pub(crate) current_signer_count: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum IdentityCheck {
    Verified,
    InvalidPackage,
    InvalidVersion,
    UnverifiedSignature,
    InvalidSigner,
    SigningCertificateMismatch,
}

fn valid_sha256_fingerprint(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b|
        b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// This function is a *necessary*, not sufficient, prerequisite to install.
/// No path, version or certificate supplied from the Tauri frontend can be
/// accepted as PlatformApkIdentity. The Android bridge remains required.
pub(crate) fn check_platform_apk_identity(
    installed: &PlatformApkIdentity,
    candidate: &PlatformApkIdentity,
) -> IdentityCheck {
    if installed.package_name != WORKER_PACKAGE_ID ||
        candidate.package_name != WORKER_PACKAGE_ID {
        return IdentityCheck::InvalidPackage;
    }
    if installed.long_version_code == 0 ||
        candidate.long_version_code <= installed.long_version_code {
        return IdentityCheck::InvalidVersion;
    }
    if !installed.platform_signing_verified || !candidate.platform_signing_verified {
        return IdentityCheck::UnverifiedSignature;
    }
    if installed.current_signer_count != 1 || candidate.current_signer_count != 1 ||
        !valid_sha256_fingerprint(&installed.certificate_sha256) ||
        !valid_sha256_fingerprint(&candidate.certificate_sha256) {
        return IdentityCheck::InvalidSigner;
    }
    if installed.certificate_sha256 != candidate.certificate_sha256 {
        return IdentityCheck::SigningCertificateMismatch;
    }
    IdentityCheck::Verified
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(version: u64) -> PlatformApkIdentity {
        PlatformApkIdentity {
            package_name: WORKER_PACKAGE_ID.to_owned(),
            long_version_code: version,
            certificate_sha256: "a".repeat(64),
            platform_signing_verified: true,
            current_signer_count: 1,
        }
    }

    #[test]
    fn allows_only_newer_identically_signed_worker() {
        assert_eq!(check_platform_apk_identity(&record(100), &record(101)),
                   IdentityCheck::Verified);
    }

    #[test]
    fn rejects_wrong_installed_or_candidate_package() {
        let mut installed = record(100);
        installed.package_name = "pl.other.worker".into();
        assert_eq!(check_platform_apk_identity(&installed, &record(101)),
                   IdentityCheck::InvalidPackage);
        let mut candidate = record(101);
        candidate.package_name = "pl.other.worker".into();
        assert_eq!(check_platform_apk_identity(&record(100), &candidate),
                   IdentityCheck::InvalidPackage);
    }

    #[test]
    fn rejects_equal_lower_or_zero_version_code() {
        for (installed, candidate) in [(100, 100), (100, 99), (0, 1),
                                       (100, 0)] {
            assert_eq!(check_platform_apk_identity(&record(installed), &record(candidate)),
                       IdentityCheck::InvalidVersion);
        }
    }

    #[test]
    fn rejects_missing_platform_authentication() {
        let mut candidate = record(101);
        candidate.platform_signing_verified = false;
        assert_eq!(check_platform_apk_identity(&record(100), &candidate),
                   IdentityCheck::UnverifiedSignature);
        let mut installed = record(100);
        installed.platform_signing_verified = false;
        assert_eq!(check_platform_apk_identity(&installed, &record(101)),
                   IdentityCheck::UnverifiedSignature);
    }

    #[test]
    fn rejects_multiple_missing_or_malformed_current_signers() {
        for count in [0, 2, 3] {
            let mut candidate = record(101);
            candidate.current_signer_count = count;
            assert_eq!(check_platform_apk_identity(&record(100), &candidate),
                       IdentityCheck::InvalidSigner);
        }
        for digest in ["".to_owned(), "x".to_owned(), "A".repeat(64), "0".repeat(63)] {
            let mut candidate = record(101);
            candidate.certificate_sha256 = digest.into();
            assert_eq!(check_platform_apk_identity(&record(100), &candidate),
                       IdentityCheck::InvalidSigner);
        }
    }

    #[test]
    fn rejects_changed_certificate_even_when_release_hash_matches() {
        let mut candidate = record(101);
        candidate.certificate_sha256 = "b".repeat(64);
        assert_eq!(check_platform_apk_identity(&record(100), &candidate),
                   IdentityCheck::SigningCertificateMismatch);
    }
}
