"""Panel-only Hugging Face candidate review and audit operations (#314).

Only the authenticated web-panel HTTP router may call this adapter after the
browser session and Origin checks. Audit exclusion and locking remain owned
by the existing domain services; Worker machine tokens are not accepted.
"""
import hf_candidate_audit
import hf_candidate_reviews
import league

HF_REVIEW_POST_ROUTES = frozenset({
    "/api/hf-candidates/review",
    "/api/hf-candidates/review-action",
    "/api/hf-candidates/audit",
})


def handle_hf_review_admin_post(path, payload, *, actor):
    """Handle one exact admin route using the existing shared services."""
    if path == "/api/hf-candidates/review":
        with league.connect() as connection:
            return hf_candidate_reviews.save(
                connection, payload, actor=actor or "arena")
    if path == "/api/hf-candidates/review-action":
        with league.connect() as connection:
            return hf_candidate_reviews.apply_proposal_action(
                connection,
                proposal_id=payload.get("proposal_id"),
                action=payload.get("action"),
                reason=payload.get("reason", ""),
                actor=actor or "arena",
                catalog=hf_candidate_audit.load_catalog(),
            )
    if path == "/api/hf-candidates/audit":
        if not hf_candidate_audit.AUDIT_LOCK.acquire(blocking=False):
            raise ValueError(
                "Inny audyt Hugging Face już trwa; spróbuj ponownie za chwilę."
            )
        try:
            report = hf_candidate_audit.audit_candidate(payload.get("catalog_id"))
            with league.connect() as connection:
                hf_candidate_audit.save_report(connection, report)
            return report
        finally:
            hf_candidate_audit.AUDIT_LOCK.release()
    raise ValueError("Unknown HF review admin route")
