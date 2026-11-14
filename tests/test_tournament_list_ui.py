import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


class TournamentListUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = HTML.index("function renderTournaments(rows)")
        end = HTML.index("\nfunction renderModelStatus", start)
        cls.renderer = HTML[start:end]

    def test_tournament_details_are_separate_semantic_fields(self):
        self.assertIn("tournament-card__title", self.renderer)
        self.assertIn("tournament-card__id", self.renderer)
        self.assertIn("tournament-status", self.renderer)
        self.assertIn("tournament-card__fields", self.renderer)
        self.assertIn("addField(fields,'Format'", self.renderer)
        self.assertIn("addField(fields,'Uczestnicy',Number(t.players??0))", self.renderer)
        self.assertIn("addField(fields,'Postęp partii'", self.renderer)
        self.assertIn("addField(fields,'Awans do draftu'", self.renderer)
        self.assertIn("addField(fields,'Checkpoint'", self.renderer)
        self.assertIn("document.createElement('dt')", self.renderer)
        self.assertIn("document.createElement('dd')", self.renderer)

    def test_status_is_textual_not_color_only(self):
        self.assertIn("statusLabels", self.renderer)
        self.assertIn("status.textContent=", self.renderer)
        self.assertIn('data-status="running"', HTML)
        self.assertIn('data-status="failed"', HTML)

    def test_existing_actions_and_visibility_conditions_are_preserved(self):
        for action in ("Tabela i drabinka", "Dlaczego nie startuje?", "Ponów diagnostykę", "Start zablokowany", "Uruchom turniej", "Wznów turniej", "Zatrzymaj turniej"):
            self.assertIn(action, self.renderer)
        self.assertIn("loadTournamentView(t.id)", self.renderer)
        self.assertIn("if(['planned','stopped','failed'].includes(t.status))", self.renderer)
        self.assertIn("'/api/tournaments/'+t.id+'/preflight'", self.renderer)
        self.assertIn("if(['planned','stopped','failed'].includes(t.status))", self.renderer)
        self.assertIn("if(!readiness.ready){await showPreflight()", self.renderer)
        self.assertIn("Otwórz warunki dopuszczenia", self.renderer)
        self.assertIn("Otwórz rejestr modeli i licencje", self.renderer)
        self.assertIn("Uzupełnij dane modelu '+e.model_id", self.renderer)
        self.assertIn("Brak lokalnego GGUF nie blokuje startu", self.renderer)
        self.assertIn("'/api/tournaments/start'", self.renderer)
        self.assertIn("'/api/tournaments/stop'", self.renderer)
        self.assertIn("if(['planned','stopped','failed'].includes(t.status)&&!t.resume_blocked)", self.renderer)
        self.assertIn("wznowienie zablokowane — wymagany przegląd", self.renderer)
        self.assertIn("if(t.status==='running')", self.renderer)

    def test_diagnostics_are_available_for_unstarted_and_failed_tournaments(self):
        self.assertIn("if(['planned','stopped','failed'].includes(t.status))", self.renderer)
        self.assertIn("check.textContent='Dlaczego nie startuje?'", self.renderer)
        self.assertIn("check.onclick=showPreflight", self.renderer)
        self.assertIn("if(t.status==='planned')showPreflight()", self.renderer)
        self.assertIn("if(t.status==='running')", self.renderer)
        self.assertIn("completed:'Zakończony'", self.renderer)

    def test_prestart_addition_and_poststart_elimination_controls_are_present(self):
        self.assertIn("Dodaj nowych zawodników bez limitu przed startem", HTML)
        self.assertIn("'/api/tournaments/entries'", HTML)
        self.assertIn("Kandydaci do osobnej eliminacji", HTML)
        self.assertIn("'/api/tournaments/admissions'", HTML)
        self.assertIn("co najmniej jedno zwycięstwo", HTML)
        self.assertIn("Historia kwalifikacji nowych zawodników", HTML)
        self.assertIn("capacity_exhausted", HTML)

    def test_empty_list_state_is_preserved(self):
        self.assertIn("if(!rows.length){host.textContent='Brak turniejów.';return}", self.renderer)

    def test_mobile_layout_and_keyboard_focus_are_defined(self):
        self.assertIn(".tournament-card__fields{grid-template-columns:repeat(2,minmax(0,1fr))}", HTML)
        self.assertIn(".tournament-card__actions button{flex:1 1 100%}", HTML)
        self.assertIn(".tournament-card button:focus-visible", HTML)
        self.assertRegex(HTML, r"@media\(max-width:760px\)")
        self.assertIn("overflow-x:hidden", HTML)

    def test_renderer_uses_dom_text_nodes_for_model_data(self):
        self.assertIn("title.textContent=t.name||'Turniej bez nazwy'", self.renderer)
        self.assertIn("detail.textContent=String(value)", self.renderer)
        self.assertNotIn("innerHTML", self.renderer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
