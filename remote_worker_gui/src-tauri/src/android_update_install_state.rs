//! Pure fail-closed Android PackageInstaller lifecycle policy.
//!
//! PackageInstaller may return STATUS_PENDING_USER_ACTION and finish only
//! after the user's confirmation. A successful Session.commit() call is not
//! the result of installation. Do not release the coordinator hold merely
//! because the Activity was backgrounded, destroyed or restarted.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InstallPhase {
    VerifiedStaged,
    CoordinatorReserved,
    SessionCreated,
    SessionCommitted,
    AwaitingUserConsent,
    ResultUnknown,
    InstalledConfirmed,
    CancelledConfirmed,
    FailedConfirmed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InstallEvidence {
    AuthenticatedReservation,
    DurableSessionCreated,
    SessionCommitted,
    PendingUserAction,
    OsInstalledVersionAndSigningVerified,
    OsCancelledSessionVerified,
    OsFailedSessionVerified,
    RestartWithoutConclusiveEvidence,
}

/// A later transition never treats an omitted callback as success.
/// The OS-installed version/certificate must independently be re-read from
/// PackageManager before successful completion can be declared.
pub(crate) fn next_phase(
    current: InstallPhase,
    evidence: InstallEvidence,
) -> Result<InstallPhase, &'static str> {
    use InstallEvidence::*;
    use InstallPhase::*;
    match (current, evidence) {
        (VerifiedStaged, AuthenticatedReservation) => Ok(CoordinatorReserved),
        (CoordinatorReserved, DurableSessionCreated) => Ok(SessionCreated),
        (SessionCreated, SessionCommitted) => Ok(SessionCommitted),
        (SessionCommitted, PendingUserAction) => Ok(AwaitingUserConsent),
        (SessionCommitted | AwaitingUserConsent | ResultUnknown,
            OsInstalledVersionAndSigningVerified) => Ok(InstalledConfirmed),
        (CoordinatorReserved | SessionCreated | SessionCommitted |
            AwaitingUserConsent | ResultUnknown, OsCancelledSessionVerified) =>
                Ok(CancelledConfirmed),
        (CoordinatorReserved | SessionCreated | SessionCommitted |
            AwaitingUserConsent | ResultUnknown, OsFailedSessionVerified) =>
                Ok(FailedConfirmed),
        (CoordinatorReserved | SessionCreated | SessionCommitted |
            AwaitingUserConsent | ResultUnknown, RestartWithoutConclusiveEvidence) =>
                Ok(ResultUnknown),
        _ => Err("Niepotwierdzona zmiana stanu instalacji APK"),
    }
}

pub(crate) fn may_request_system_install(
    current: InstallPhase,
    coordinator_hold_valid: bool,
    sha256_matches: bool,
    android_signature_matches: bool,
    version_increased: bool,
    os_permission: bool,
) -> bool {
    current == InstallPhase::CoordinatorReserved
        && coordinator_hold_valid
        && sha256_matches
        && android_signature_matches
        && version_increased
        && os_permission
}

/// Terminal OS confirmation only. This by itself does NOT release a hold:
/// the coordinator still has to return authenticated confirmation and set
/// worker state unknown until post-update reconciliation.
pub(crate) fn may_request_hold_release(current: InstallPhase) -> bool {
    matches!(current, InstallPhase::InstalledConfirmed |
        InstallPhase::CancelledConfirmed | InstallPhase::FailedConfirmed)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normal_user_confirmed_install_must_pass_all_evidence_gates() {
        use InstallEvidence::*;
        use InstallPhase::*;
        let mut phase = VerifiedStaged;
        phase = next_phase(phase, AuthenticatedReservation).unwrap();
        assert_eq!(phase, CoordinatorReserved);
        assert!(!may_request_system_install(phase, true, true, true, true, false));
        assert!(!may_request_system_install(phase, true, true, true, false, true));
        assert!(!may_request_system_install(phase, true, true, false, true, true));
        assert!(!may_request_system_install(phase, true, false, true, true, true));
        assert!(!may_request_system_install(phase, false, true, true, true, true));
        assert!(may_request_system_install(phase, true, true, true, true, true));
        phase = next_phase(phase, DurableSessionCreated).unwrap();
        phase = next_phase(phase, SessionCommitted).unwrap();
        assert!(!may_request_hold_release(phase));
        phase = next_phase(phase, PendingUserAction).unwrap();
        assert!(!may_request_hold_release(phase));
        phase = next_phase(phase, OsInstalledVersionAndSigningVerified).unwrap();
        assert!(may_request_hold_release(phase));
    }

    #[test]
    fn interrupted_installer_does_not_allow_hold_release_or_retry() {
        use InstallEvidence::*;
        use InstallPhase::*;
        for phase in [CoordinatorReserved, SessionCreated, SessionCommitted, AwaitingUserConsent] {
            let unknown = next_phase(phase, RestartWithoutConclusiveEvidence).unwrap();
            assert_eq!(unknown, ResultUnknown);
            assert!(!may_request_hold_release(unknown));
            assert!(!may_request_system_install(unknown, true, true, true, true, true));
        }
        assert!(next_phase(ResultUnknown, PendingUserAction).is_err());
    }

    #[test]
    fn only_os_verified_terminal_outcome_can_release_hold() {
        use InstallEvidence::*;
        use InstallPhase::*;
        for (event, expected) in [
            (OsInstalledVersionAndSigningVerified, InstalledConfirmed),
            (OsCancelledSessionVerified, CancelledConfirmed),
            (OsFailedSessionVerified, FailedConfirmed),
        ] {
            let terminal = next_phase(ResultUnknown, event).unwrap();
            assert_eq!(terminal, expected);
            assert!(may_request_hold_release(terminal));
            assert!(next_phase(terminal, SessionCommitted).is_err());
        }
    }

    #[test]
    fn forged_and_out_of_order_events_cannot_skip_reservation() {
        use InstallEvidence::*;
        use InstallPhase::*;
        for event in [DurableSessionCreated, SessionCommitted, PendingUserAction,
            OsInstalledVersionAndSigningVerified, OsCancelledSessionVerified,
            OsFailedSessionVerified, RestartWithoutConclusiveEvidence] {
            assert!(next_phase(VerifiedStaged, event).is_err());
        }
        assert!(!may_request_system_install(VerifiedStaged, true, true, true, true, true));
    }
}
