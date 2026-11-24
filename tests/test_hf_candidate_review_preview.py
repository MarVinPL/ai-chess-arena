from pathlib import Path
import unittest


class CandidateReviewPreviewFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / 'index.html').read_text(encoding='utf-8') + '\n' + (Path(__file__).resolve().parents[1] / 'web/css/arena.css').read_text(encoding='utf-8') + '\n' + (Path(__file__).resolve().parents[1] / 'web/js/arena.js').read_text(encoding='utf-8')
        start = cls.html.index("function renderHfCandidateReviewForm(")
        end = cls.html.index("async function loadHfCandidates()", start)
        cls.review_form = cls.html[start:end]
        proposal_start = cls.html.index("const reviewOpen=$('model-review-open')")
        proposal_end = cls.html.index('async function loadModelReviewSubmissions()', proposal_start)
        cls.proposal_form = cls.html[proposal_start:proposal_end]

    def test_review_requires_preview_then_separate_confirmation(self):
        self.assertIn("Pokaż podsumowanie zmian", self.review_form)
        self.assertIn("Potwierdź i zapisz ustalenia", self.review_form)
        preview = self.review_form.split('save.onclick=()=>{', 1)[1].split('confirmSave.onclick=', 1)[0]
        self.assertNotIn("fetch('/api/hf-candidates/review'", preview)
        confirm = self.review_form.split('confirmSave.onclick=', 1)[1]
        self.assertIn("fetch('/api/hf-candidates/review'", confirm)

    def test_preview_shows_field_level_before_and_after_values(self):
        self.assertIn('Podsumowanie zmian przed zapisem', self.review_form)
        self.assertIn("przed: ", self.review_form)
        self.assertIn(" → po: ", self.review_form)
        self.assertIn('report.pinned_revision', self.review_form)
        self.assertIn('Pochodzenie, pewność i decyzja wymagane dla każdego pola', self.html)
        self.assertIn('field_provenance:report.field_provenance', self.html)

    def test_editing_after_preview_invalidates_confirmation(self):
        self.assertIn('invalidatePreview', self.review_form)
        self.assertIn("addEventListener('input',invalidatePreview)", self.review_form)
        self.assertIn("addEventListener('change',invalidatePreview)", self.review_form)
        self.assertIn('if(!pendingPayload)return', self.review_form)


    def test_admission_proposal_requires_preview_then_explicit_save(self):
        self.assertIn('Podsumowanie propozycji przed zapisem', self.proposal_form)
        self.assertIn('Potwierdź i zapisz propozycję', self.proposal_form)
        submit = self.proposal_form.split("reviewForm.addEventListener('submit',event=>", 1)[1].split("const confirm=document.createElement('button')", 1)[0]
        self.assertNotIn("fetch(editingId?'/api/model-review-submissions/action'", submit)
        confirmation = self.proposal_form.split("const confirm=document.createElement('button')", 1)[1]
        self.assertIn("fetch(editingId?'/api/model-review-submissions/action'", confirmation)

    def test_proposal_changes_invalidate_preview_and_show_diff_for_edits(self):
        self.assertIn('clearReviewPreview', self.proposal_form)
        self.assertIn("addEventListener('input'", self.proposal_form)
        self.assertIn("addEventListener('change'", self.proposal_form)
        self.assertIn("reviewForm.dataset.originalProposal=JSON.stringify(p)", self.html)
        self.assertIn('przed: '+"'", self.proposal_form)


    def test_candidate_queue_filters_by_model_and_review_status(self):
        self.assertIn('id=\"hf-candidate-query\"', self.html)
        self.assertIn('id=\"hf-candidate-status-filter\"', self.html)
        self.assertIn("statusFilter==='needs_review'", self.html)
        self.assertIn("statusFilter==='review_in_progress'", self.html)
        self.assertIn("statusFilter==='inactive'", self.html)
        self.assertIn("addEventListener('change',renderHfCandidates)", self.html)

    def test_review_remains_evidence_only_and_does_not_admit_model(self):
        self.assertIn('nie zmienia allowlisty', self.review_form)
        self.assertIn('nie dopuszcza modelu do turnieju', self.review_form)


if __name__ == '__main__':
    unittest.main()
