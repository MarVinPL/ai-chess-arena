//! Fail-closed preflight for the future Android GGUF preparation phase.
//! A chess inference assignment is NOT a durable permission to spend hours
//! downloading a model: the inference deadline may expire first.
//! This module performs no I/O or eviction and grants NO transfer capability.

use crate::android_model_cache::DEFAULT_FREE_RESERVE_BYTES;
use crate::android_model_source::{bind_approved_model_to_job, ApprovedGguf};

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ModelPreflight {
    /// Exact on-disk GGUF SHA, GGUF header and bytes must already be verified.
    VerifiedCacheAvailable,
    /// No files were touched. A separate coordinator-owned preparation lease
    /// must be implemented and verified before invoking QUIC or HTTPS transfer.
    NeedsPreparationLease {
        required_bytes: u64,
        available_bytes: u64,
    },
    /// Preserve at least 10 GiB free and never silently evict another model.
    InsufficientStorage {
        required_bytes: u64,
        available_bytes: u64,
    },
}

/// This is a *planner*, not permission to download or to use a model.
/// `verified_cache` may be true ONLY after hashing the actual app-owned file.
/// A currently running game, update hold, STOP or uncertain recovery all
/// prevent a new model download; neither a WebView value nor a llama alias is
/// acceptable evidence that the phone is idle.
pub(crate) fn preflight_assigned_gguf(
    assigned_sha256: &str,
    approved: &ApprovedGguf,
    usable_bytes: u64,
    verified_cache: bool,
    active_inference_or_job: bool,
    stop_or_update_pending: bool,
) -> Result<ModelPreflight, String> {
    bind_approved_model_to_job(assigned_sha256, approved)?;
    if active_inference_or_job || stop_or_update_pending {
        return Err("Aktywny przydział, STOP lub aktualizacja blokują zmianę modelu".into());
    }
    // No network operation, allocation, or deletion takes place here.
    if verified_cache {
        return Ok(ModelPreflight::VerifiedCacheAvailable);
    }
    let needed = approved.size_bytes.checked_add(DEFAULT_FREE_RESERVE_BYTES)
        .ok_or("Przekroczono rozmiar pamięci modelu")?;
    if usable_bytes < needed {
        return Ok(ModelPreflight::InsufficientStorage {
            required_bytes: needed, available_bytes: usable_bytes,
        });
    }
    Ok(ModelPreflight::NeedsPreparationLease {
        required_bytes: needed, available_bytes: usable_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn approved() -> ApprovedGguf {
        ApprovedGguf {
            sha256: "a".repeat(64),
            size_bytes: 3 * 1024 * 1024 * 1024,
            source_url: format!(
                "https://huggingface.co/owner/repo/resolve/{}/model.Q4.gguf",
                "1".repeat(40)),
        }
    }
    #[test]
    fn sufficient_space_still_requires_independent_preparation_lease() {
        let model = approved();
        let free = 14 * 1024 * 1024 * 1024;
        assert_eq!(preflight_assigned_gguf(&model.sha256, &model, free,
            false, false, false).unwrap(),
            ModelPreflight::NeedsPreparationLease {
                required_bytes: 13 * 1024 * 1024 * 1024,
                available_bytes: free,
            });
    }
    #[test]
    fn insufficient_free_bytes_never_trigger_eviction() {
        let model = approved();
        let free = 12 * 1024 * 1024 * 1024;
        assert_eq!(preflight_assigned_gguf(&model.sha256, &model, free,
            false, false, false).unwrap(),
            ModelPreflight::InsufficientStorage {
                required_bytes: 13 * 1024 * 1024 * 1024,
                available_bytes: free,
            });
    }
    #[test]
    fn active_or_uncertain_execution_always_blocks_even_when_cached() {
        let model = approved();
        for active in [false, true] {
            for stop in [false, true] {
                if active || stop {
                    assert!(preflight_assigned_gguf(
                        &model.sha256, &model, u64::MAX, true, active, stop
                    ).is_err());
                }
            }
        }
    }
    #[test]
    fn cache_can_be_reused_only_with_independent_verified_hash_evidence() {
        let model = approved();
        assert_eq!(preflight_assigned_gguf(&model.sha256, &model, 0,
            true, false, false).unwrap(), ModelPreflight::VerifiedCacheAvailable);
        assert_eq!(preflight_assigned_gguf(&model.sha256, &model, 0,
            false, false, false).unwrap(),
            ModelPreflight::InsufficientStorage {
                required_bytes: 13 * 1024 * 1024 * 1024,
                available_bytes: 0,
            });
    }
    #[test]
    fn wrong_assignment_or_untrusted_source_is_rejected() {
        let model = approved();
        assert!(preflight_assigned_gguf(&"b".repeat(64), &model,
            u64::MAX, false, false, false).is_err());
        let mut mutable = model.clone();
        mutable.source_url = mutable.source_url.replace(&"1".repeat(40), "main");
        assert!(preflight_assigned_gguf(&model.sha256, &mutable,
            u64::MAX, false, false, false).is_err());
    }
}
