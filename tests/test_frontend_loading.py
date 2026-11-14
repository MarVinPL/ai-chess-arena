import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


class TournamentLoadingRegressionTests(unittest.TestCase):
    def test_tournaments_are_loaded_independently_of_storage(self):
        start = HTML.index("async function loadLeague()")
        end = HTML.index("\nasync function addModelVariant", start)
        loader = HTML[start:end]

        self.assertIn("Promise.allSettled", loader)
        self.assertIn("renderTournaments(tournaments)", loader)
        self.assertIn("if(storage){", loader)
        self.assertIn("Stan magazynu niedostępny.", loader)
        self.assertLess(
            loader.index("renderTournaments(tournaments)"),
            loader.index("$('storage-info').textContent"),
        )

    def test_http_errors_are_not_treated_as_successful_json(self):
        start = HTML.index("async function loadLeague()")
        end = HTML.index("\nasync function addModelVariant", start)
        loader = HTML[start:end]

        self.assertIn("if(!r.ok)throw Error(u+' HTTP '+r.status)", loader)

    def test_html_document_has_closing_tags(self):
        self.assertTrue(HTML.rstrip().endswith("</script></body></html>"))


if __name__ == "__main__":
    unittest.main()
