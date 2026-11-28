//! Internal, advisory Android evidence. No IPC and no journal mutation.
use crate::android_update_identity::{PlatformApkIdentity, WORKER_PACKAGE_ID, WORKER_SIGNING_CERT_SHA256};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InstallHistory { FirstInstallHint, Updated, Unknown }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Session { NoneVisible, Active(i32), Ambiguous }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Outcome { NoTerminalEvidence, Pending, InstalledCandidate, FailedCandidate, Uncertain }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ExpectedInstall {
    pub(crate) session_id: i32,
    pub(crate) previous: u64,
    pub(crate) target: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct InstallEvidence {
    identity: PlatformApkIdentity,
    history: InstallHistory,
    outcome: Outcome,
    expected: Option<ExpectedInstall>,
    session: Session,
    callback: Option<(i32, i32)>,
}

impl InstallEvidence {
    pub(crate) fn identity(&self) -> &PlatformApkIdentity { &self.identity }
    pub(crate) fn history(&self) -> InstallHistory { self.history }
    pub(crate) fn outcome(&self) -> Outcome { self.outcome }
    pub(crate) fn expected(&self) -> Option<ExpectedInstall> { self.expected }
}

fn evaluate(identity: PlatformApkIdentity, times: (i64, i64), session: Session,
            callback: Option<(i32, i32)>, expected: Option<ExpectedInstall>)
            -> Result<InstallEvidence, String> {
    if identity.package_name != WORKER_PACKAGE_ID || identity.long_version_code == 0
        || !identity.platform_signing_verified || identity.current_signer_count != 1
        || identity.certificate_sha256 != WORKER_SIGNING_CERT_SHA256 {
        return Err("Niepotwierdzona tożsamość zainstalowanego APK".into());
    }
    let history = match times {
        (first, last) if first > 0 && first == last => InstallHistory::FirstInstallHint,
        (first, last) if first > 0 && last > first => InstallHistory::Updated,
        _ => InstallHistory::Unknown,
    };
    let outcome = match (session, callback, expected) {
        (Session::NoneVisible, None, None) => Outcome::NoTerminalEvidence,
        (Session::Active(id), cb, Some(e)) if id >= 0 && id == e.session_id
            && e.previous > 0 && e.target > e.previous
            && identity.long_version_code == e.previous
            && (cb.is_none() || cb == Some((id, -1))) => Outcome::Pending,
        (Session::NoneVisible, Some((id, status)), Some(e))
            if id >= 0 && id == e.session_id && e.previous > 0 && e.target > e.previous => {
                match status {
                    0 if identity.long_version_code == e.target => Outcome::InstalledCandidate,
                    1..=8 if identity.long_version_code == e.previous => Outcome::FailedCandidate,
                    _ => Outcome::Uncertain,
                }
            }
        _ => Outcome::Uncertain,
    };
    Ok(InstallEvidence { identity, history, outcome, expected, session, callback })
}

#[cfg(target_os = "android")]
pub(crate) fn collect(expected: Option<&crate::android_update_install_journal::PendingInstall>)
    -> Result<InstallEvidence, String> {
    use crate::{android_update_package_manager as pm, android_update_package_installer as pi};
    use pi::SessionObservation as S;
    let started = std::time::Instant::now();
    let identity = pm::inspect_installed_worker_identity()?;
    let times = pm::inspect_install_times()?;
    let session = pi::observe_own_session_details()?;
    let callback = pi::read_callback_snapshot()?;
    // Detect observable changes during the read. This is not a lock or fence.
    if identity != pm::inspect_installed_worker_identity()?
        || times != pm::inspect_install_times()?
        || session != pi::observe_own_session_details()?
        || callback != pi::read_callback_snapshot()?
        || started.elapsed() > std::time::Duration::from_secs(3) {
        return Err("Stan instalatora zmienił się podczas odczytu".into());
    }
    let session = match session {
        (S::NoVisibleSession, None) => Session::NoneVisible,
        (S::Uncommitted | S::CommittedOrAwaitingConsent, Some(id)) => Session::Active(id),
        _ => Session::Ambiguous,
    };
    evaluate(identity, times, session, callback, expected.map(|e| ExpectedInstall {
        session_id: e.session_id, previous: e.previous_version_code, target: e.target_version_code,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn identity(version: u64) -> PlatformApkIdentity {
        PlatformApkIdentity { package_name: WORKER_PACKAGE_ID.into(), long_version_code: version,
            certificate_sha256: WORKER_SIGNING_CERT_SHA256.into(), platform_signing_verified: true,
            current_signer_count: 1 }
    }
    const E: ExpectedInstall = ExpectedInstall { session_id: 7, previous: 10, target: 11 };
    #[test]
    fn history_never_proves_clean_store() {
        for (times, history) in [((1,1), InstallHistory::FirstInstallHint),
            ((1,2), InstallHistory::Updated), ((0,0), InstallHistory::Unknown),
            ((2,1), InstallHistory::Unknown)] {
            let e = evaluate(identity(10), times, Session::NoneVisible, None, None).unwrap();
            assert_eq!(e.history(), history);
            assert_eq!(e.outcome(), Outcome::NoTerminalEvidence);
        }
    }
    #[test]
    fn missing_session_or_callback_never_confirms_update() {
        for (s,c) in [(Session::NoneVisible,None), (Session::Ambiguous,Some((7,0))),
            (Session::Active(7),Some((7,0))), (Session::NoneVisible,Some((8,0))),
            (Session::NoneVisible,Some((7,-1))), (Session::NoneVisible,Some((7,99)))] {
            assert_eq!(evaluate(identity(11),(1,2),s,c,Some(E)).unwrap().outcome(), Outcome::Uncertain);
        }
    }
    #[test]
    fn terminal_requires_matching_callback_and_version() {
        for (v,status,outcome) in [(11,0,Outcome::InstalledCandidate),(10,0,Outcome::Uncertain),
            (12,0,Outcome::Uncertain),(10,4,Outcome::FailedCandidate),(11,4,Outcome::Uncertain)] {
            assert_eq!(evaluate(identity(v),(1,2),Session::NoneVisible,Some((7,status)),Some(E))
                .unwrap().outcome(),outcome);
        }
    }
    #[test]
    fn rejects_wrong_identity_and_ambiguous_signer() {
        let mut i=identity(11); i.certificate_sha256="a".repeat(64);
        assert!(evaluate(i,(1,2),Session::NoneVisible,Some((7,0)),Some(E)).is_err());
        let mut i=identity(11); i.current_signer_count=2;
        assert!(evaluate(i,(1,2),Session::NoneVisible,Some((7,0)),Some(E)).is_err());
        let mut i=identity(11); i.package_name="other".into();
        assert!(evaluate(i,(1,2),Session::NoneVisible,Some((7,0)),Some(E)).is_err());
    }
    #[test]
    fn malformed_expectations_and_unverified_identity_block() {
        for e in [ExpectedInstall { session_id: -1, ..E },
                  ExpectedInstall { previous: 0, ..E },
                  ExpectedInstall { target: 10, ..E }] {
            assert_eq!(evaluate(identity(11),(1,2),Session::NoneVisible,Some((7,0)),Some(e))
                .unwrap().outcome(), Outcome::Uncertain);
        }
        let mut i=identity(11); i.platform_signing_verified=false;
        assert!(evaluate(i,(1,2),Session::NoneVisible,Some((7,0)),Some(E)).is_err());
        assert!(evaluate(identity(0),(1,2),Session::NoneVisible,None,None).is_err());
    }
    #[test]
    fn active_session_never_terminal_and_orphan_callback_blocks() {
        assert_eq!(evaluate(identity(10),(1,2),Session::Active(7),Some((7,-1)),Some(E))
            .unwrap().outcome(), Outcome::Pending);
        assert_eq!(evaluate(identity(10),(1,2),Session::NoneVisible,Some((7,0)),None)
            .unwrap().outcome(), Outcome::Uncertain);
    }
}
