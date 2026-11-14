import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')


class ModelAdmissionFrontendTests(unittest.TestCase):
    def test_registry_has_separate_local_admission_area(self):
        self.assertIn('id="model-admission-list"', HTML)
        self.assertIn('Nie zmienia allowlisty turniejowej', HTML)

    def test_admit_and_revoke_actions_require_confirmation_and_refresh(self):
        self.assertIn("'/api/model-admission/admit'", HTML)
        self.assertIn("'/api/model-admission/revoke'", HTML)
        self.assertIn('confirm:true', HTML)
        self.assertIn('await loadLeague()', HTML)
        self.assertIn('Podaj uzasadnienie wycofania', HTML)

    def test_registry_list_is_rendered_with_text_content(self):
        self.assertIn('function renderModelAdmission(items)', HTML)
        self.assertIn('title.textContent', HTML)
        self.assertIn('item.history.map', HTML)


if __name__ == '__main__':
    unittest.main()
