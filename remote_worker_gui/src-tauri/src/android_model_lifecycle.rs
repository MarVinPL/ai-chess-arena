//! Fail-closed lifecycle gate for Android GGUF transfers and cache eviction.
//!
//! This pure policy code has NO filesystem effects, dispatch or Tauri command.
//! An authenticated coordinator snapshot is evidence, not a lease lock:
//! callers must serialize execution and recheck the coordinator immediately
//! before each future destructive operation. Restart/UNKNOWN stays blocked.
use crate::android_model_cache::{
    plan_storage_reclamation, valid_digest, CacheEntry, StoragePlan,
};

const MAX_EVIDENCE_AGE_MS: u64 = 30_000;
const MAX_CLOCK_SKEW_MS: u64 = 5_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum JobLifecycle {
    /// Coordinator hasn't reconciled restart/transport/session state.
    Unknown,
    /// A model is executing or an Arena assignment is still live.
    Running,
    /// STOP was requested but worker hasn't acknowledged checkpoint.
    StopRequested,
    /// STOP acknowledgement alone is NOT an Arena terminal commit.
    StopAcknowledged,
    /// Arena durably committed the final checkpoint and confirmed no job.
    IdleConfirmed,
}

#[derive(Debug, Clone)]
pub(crate) struct CoordinatorLeaseEvidence {
    pub(crate) state: JobLifecycle,
    pub(crate) checked_at_ms: u64,
    pub(crate) authenticated: bool,
    pub(crate) worker_matches: bool,
    pub(crate) active_job_id: Option<String>,
    pub(crate) assigned_model_sha256: Option<String>,
    pub(crate) update_hold: bool,
    pub(crate) pause_pending: bool,
    pub(crate) resume_pending: bool,
    /// Evidence that the relevant inference process/FD is closed.
    pub(crate) runtime_stopped: bool,
    /// All pending checkpoint/result receipts were durably finalized.
    pub(crate) checkpoint_finalized: bool,
    /// From a fresh, independent coordinator reconciliation after STOP.
    pub(crate) terminal_reconciled: bool,
}

fn evidence_fresh(e: &CoordinatorLeaseEvidence, now_ms: u64) -> bool {
    if !e.authenticated || !e.worker_matches
        || e.update_hold || e.pause_pending || e.resume_pending {
        return false;
    }
    if e.checked_at_ms > now_ms {
        e.checked_at_ms - now_ms <= MAX_CLOCK_SKEW_MS
    } else {
        now_ms - e.checked_at_ms <= MAX_EVIDENCE_AGE_MS
    }
}

/// Future download admission for the *exact* active job. This gate never
/// accepts an idle capability, a stale alias, or a stop-pending assignment.
/// It is necessary but NOT sufficient: the pinned registry entry, actual
/// free space and a serialized lease must also be checked.
pub(crate) fn model_download_gate(
    e: &CoordinatorLeaseEvidence, now_ms: u64, expected_sha256: &str,
) -> Result<(), String> {
    if !valid_digest(expected_sha256) || !evidence_fresh(e, now_ms) {
        return Err("Brak świeżego dowodu Areny dla modelu".into());
    }
    if e.state != JobLifecycle::Running
        || e.active_job_id.as_deref().is_none_or(|id| id.is_empty())
        || e.assigned_model_sha256.as_deref() != Some(expected_sha256) {
        return Err("Brak aktywnego przydziału tego GGUF".into());
    }
    Ok(())
}

/// Eviction is stricter than downloads. STOP/ACK, timeout, disconnected
/// engine, or simple process death do NOT prove a terminal checkpoint.
/// Even when idle, both runtime closure and independent Arena reconciliation
/// are required before any specific model can be considered for LRU.
pub(crate) fn model_eviction_gate(
    e: &CoordinatorLeaseEvidence, now_ms: u64,
) -> Result<(), String> {
    if !evidence_fresh(e, now_ms)
        || e.state != JobLifecycle::IdleConfirmed
        || e.active_job_id.is_some()
        || e.assigned_model_sha256.is_some()
        || !e.runtime_stopped
        || !e.checkpoint_finalized
        || !e.terminal_reconciled {
        return Err("Niepotwierdzone zakończenie zadania; cache GGUF chroniony".into());
    }
    Ok(())
}

/// Only produces a proposed LRU plan. It never deletes a file. Per-model
/// in_use/preserve flags are independently authoritative; global idle alone
/// must never override them or implicitly release a lease.
pub(crate) fn plan_safe_cache_reclamation(
    e: &CoordinatorLeaseEvidence,
    now_ms: u64,
    free_bytes: u64,
    incoming_bytes: u64,
    reserve_bytes: u64,
    entries: &[CacheEntry],
) -> Result<StoragePlan, String> {
    model_eviction_gate(e, now_ms)?;
    plan_storage_reclamation(free_bytes, incoming_bytes, reserve_bytes, entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::android_model_cache::DEFAULT_FREE_RESERVE_BYTES;
    const NOW: u64 = 1_000_000;
    fn idle() -> CoordinatorLeaseEvidence {
        CoordinatorLeaseEvidence {
            state: JobLifecycle::IdleConfirmed,
            checked_at_ms: NOW,
            authenticated: true, worker_matches: true,
            active_job_id: None, assigned_model_sha256: None,
            update_hold: false, pause_pending: false, resume_pending: false,
            runtime_stopped: true, checkpoint_finalized: true,
            terminal_reconciled: true,
        }
    }
    fn active() -> CoordinatorLeaseEvidence {
        let mut e = idle();
        e.state = JobLifecycle::Running;
        e.active_job_id = Some("game_12345678".into());
        e.assigned_model_sha256 = Some("a".repeat(64));
        e.runtime_stopped = false;
        e.checkpoint_finalized = false;
        e.terminal_reconciled = false;
        e
    }
    #[test]
    fn exact_running_assignment_can_only_pass_download_gate() {
        let e = active();
        assert!(model_download_gate(&e, NOW, &"a".repeat(64)).is_ok());
        assert!(model_download_gate(&e, NOW, &"b".repeat(64)).is_err());
        assert!(model_download_gate(&idle(), NOW, &"a".repeat(64)).is_err());
        assert!(model_eviction_gate(&e, NOW).is_err());
    }
    #[test]
    fn stop_request_and_ack_never_authorize_eviction_or_transfer() {
        for state in [JobLifecycle::StopRequested, JobLifecycle::StopAcknowledged] {
            let mut e = active();
            e.state = state;
            assert!(model_eviction_gate(&e, NOW).is_err());
            assert!(model_download_gate(&e, NOW, &"a".repeat(64)).is_err());
        }
    }
    #[test]
    fn unknown_and_stale_evidence_always_fail_closed() {
        let mut e = idle();
        e.state = JobLifecycle::Unknown;
        assert!(model_eviction_gate(&e, NOW).is_err());
        e.state = JobLifecycle::IdleConfirmed;
        assert!(model_eviction_gate(&e, NOW + 30_001).is_err());
        assert!(model_eviction_gate(&e, NOW - 5_001).is_err());
        assert!(model_eviction_gate(&e, NOW + 30_000).is_ok());
    }
    #[test]
    fn identity_hold_pause_resume_and_missing_reconciliation_block() {
        let valid = idle();
        assert!(model_eviction_gate(&valid, NOW).is_ok());
        for change in 0..8 {
            let mut e = valid.clone();
            match change {
                0 => e.authenticated = false,
                1 => e.worker_matches = false,
                2 => e.update_hold = true,
                3 => e.pause_pending = true,
                4 => e.resume_pending = true,
                5 => e.runtime_stopped = false,
                6 => e.checkpoint_finalized = false,
                _ => e.terminal_reconciled = false,
            }
            assert!(model_eviction_gate(&e, NOW).is_err());
        }
    }
    #[test]
    fn idle_without_empty_assignment_is_not_safe() {
        let mut e = idle();
        e.active_job_id = Some("game_12345678".into());
        assert!(model_eviction_gate(&e, NOW).is_err());
        e.active_job_id = None;
        e.assigned_model_sha256 = Some("a".repeat(64));
        assert!(model_eviction_gate(&e, NOW).is_err());
    }
    #[test]
    fn eviction_planner_never_selects_active_or_preserved_gguf() {
        let gib = 1024 * 1024 * 1024;
        let entries = [
            CacheEntry {digest:"a".repeat(64),size_bytes:4*gib,last_used_ms:1,in_use:true,preserve:false},
            CacheEntry {digest:"b".repeat(64),size_bytes:3*gib,last_used_ms:2,in_use:false,preserve:true},
            CacheEntry {digest:"c".repeat(64),size_bytes:5*gib,last_used_ms:3,in_use:false,preserve:false},
        ];
        let plan = plan_safe_cache_reclamation(&idle(), NOW, 13*gib, 8*gib,
            DEFAULT_FREE_RESERVE_BYTES, &entries).unwrap();
        assert_eq!(plan.delete_digests, vec!["c".repeat(64)]);
        assert_eq!(plan.still_missing_bytes, 0);
        assert!(plan_safe_cache_reclamation(&active(), NOW, 13*gib, 8*gib,
            DEFAULT_FREE_RESERVE_BYTES, &entries).is_err());
    }
}
