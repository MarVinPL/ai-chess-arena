# Issue #188 — zapis dotychczasowych prac

Powiązane zgłoszenie: [#188 — Usprawnij odpowiedzi modeli i wdroż Szachy Eliminacyjne w silniku](https://github.com/MarVinPL/ai-chess-arena/issues/188)

Zasady wariantu zostały wcześniej opisane w [PR #187](https://github.com/MarVinPL/ai-chess-arena/pull/187), w pliku `LEAGUE.md`. PR #187 pozostaje osobnym, niepołączonym PR-em. Ten dokument zapisuje stan prac przygotowanych już w gałęzi Issue #188; nie oznacza ukończenia ani wdrożenia funkcji.

## Co zostało przygotowane w kodzie

- Dodano moduł `elimination_chess.py` z klasą `EliminationBoard`, rozszerzającą `chess.Board`.
- Wariant ma dopuścić bicie króla przeciwnika jako legalny ruch, przy zachowaniu bezpieczeństwa własnego króla.
- Brak króla jednej ze stron jest traktowany jako koniec partii; zwycięzcą zostaje strona, której król pozostał na planszy.
- Runner korzysta z `EliminationBoard` zamiast standardowego `chess.Board` przy tworzeniu nowej partii.
- Dodano dwujęzyczny (PL/EN) prompt wyjaśniający zasady Szachów Eliminacyjnych: bicie króla wygrywa, samo szach-mat nie kończy partii, a odpowiedź ma zawierać dokładnie jeden ruch UCI.
- Ograniczenie gramatyczne UCI jest przekazywane zarówno przy pierwszej próbie, jak i przy ponowieniu odpowiedzi.
- W trybie ograniczonej gramatyki wyłączono tokeny rozumowania.
- Walidacja odpowiedzi wymaga pełnego dopasowania tekstu do pojedynczego ruchu UCI; tekst objaśniający lub dodatkowa treść nie są wycinane i akceptowane jako poprawny ruch.

## Przygotowane testy

Plik `tests/test_elimination_chess.py` obejmuje m.in.:

- legalność bicia króla i wynik partii po takim ruchu;
- odrzucenie bicia króla, jeżeli ruch odsłania własnego króla;
- brak przedwczesnego zakończenia zwykłej pozycji;
- odtworzenie pozycji końcowej z FEN bez króla przeciwnika.

Zmieniono również testy runnera w `tests/test_engine_runner.py`, aby uwzględnić zmianę promptu i walidacji.

**Status weryfikacji:** w toku wcześniejszych prac odnotowano wynik 32/32 testów ukierunkowanych na tę zmianę. Nie wykonano ponownego uruchomienia testów podczas dodawania tego dokumentu; wynik należy potwierdzić ponownie przed uznaniem Issue #188 za gotowe do merge.

## Ograniczenia i dalsza weryfikacja

Ten zapis nie potwierdza jeszcze pełnej integracji wariantu z całym systemem. Przed zamknięciem Issue #188 należy zweryfikować co najmniej:

- wszystkie miejsca tworzenia i odtwarzania planszy, w tym wznowienia/checkpointy;
- zgodność generowania ruchów, SAN, PGN i FEN z wariantem;
- zachowanie API, panelu i prezentacji wyniku partii;
- komplet testów jednostkowych i integracyjnych oraz rzeczywiste odpowiedzi modeli.

Nie przeprowadzono tu testów modeli, zmian w składzie turnieju, wdrożenia produkcyjnego ani restartów usług. Nie zmieniono zasad dopuszczania ani statusu żadnego zawodnika/modelu.

## Stan repozytorium w chwili zapisu

- Gałąź robocza: `feat/issue-188-elimination-engine-20261119`.
- Zmiany kodu wymienione wyżej były już obecne w tej gałęzi jako prace robocze.
- Ten commit dokumentuje ich zakres i status; nie jest potwierdzeniem kompletności implementacji.
- Wdrożenie i merge do `main` nie są częścią tego zapisu.
