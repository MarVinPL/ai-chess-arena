import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


class TournamentLoadingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = HTML.index("async function loadLeague()")
        end = HTML.index("\nasync function addModelVariant", start)
        cls.loader = HTML[start:end]

    def test_tournaments_are_loaded_independently_of_storage(self):
        loader = self.loader
        self.assertIn("Promise.allSettled", loader)
        self.assertIn("renderTournaments(tournaments)", loader)
        self.assertIn("if(storage){", loader)
        self.assertIn("Stan magazynu niedostępny.", loader)
        self.assertLess(
            loader.index("renderTournaments(tournaments)"),
            loader.index("$('storage-info').textContent"),
        )

    def test_http_errors_are_not_treated_as_successful_json(self):
        self.assertIn("if(!r.ok)throw Error(u+' HTTP '+r.status)", self.loader)

    def test_other_failed_sections_have_explicit_errors(self):
        self.assertIn("Nie udało się odczytać części rankingu:", self.loader)
        self.assertIn("Nie udało się odczytać modeli:", self.loader)
        self.assertIn("Nie udało się odczytać turniejów:", self.loader)

    def test_html_document_has_closing_tags(self):
        self.assertTrue(HTML.rstrip().endswith("</script></body></html>"))


if __name__ == "__main__":
    unittest.main()
