# Issue #188 — zapis dotychczasowych prac

Powiązane zgłoszenie: [#188 — Usprawnij odpowiedzi modeli i wdroż Szachy Eliminacyjne w silniku](https://github.com/MarVinPL/ai-chess-arena/issues/188)

Zasady wariantu zostały wcześniej opisane w [PR #187](https://github.com/MarVinPL/ai-chess-arena/pull/187), w pliku `LEAGUE.md`. PR #187 pozostaje osobnym, niepołączonym PR-em. Ten dokument zapisuje stan prac przygotowanych już w gałęzi Issue #188; nie oznacza ukończenia ani wdrożenia funkcji.

## Co zostało przygotowane w kodzie

- Dodano moduł `elimination_chess.py` z klasą `EliminationBoard`, rozszerzającą `chess.Board`.
- Wariant dopuszcza bicie króla przeciwnika jako legalny ruch. Szach i szach-mat nie ograniczają ruchów ani nie kończą partii; własny król może pozostać pod atakiem.
- Brak króla jednej ze stron jest traktowany jako koniec partii; zwycięzcą zostaje strona, której król pozostał na planszy. Sam szach-mat i niewystarczający materiał nie kończą partii; pozostałe standardowe warunki remisowe są nadal obsługiwane. PGN po nieudanej partii odtwarza ruchy na planszy wariantu, a nie standardowej planszy szachowej.
- Runner korzysta z `EliminationBoard` zamiast standardowego `chess.Board` przy tworzeniu nowej partii. Serwerowy tryb pojedynczej partii (`ask_model`) również używa reguł wariantu, promptu priorytetu bicia króla i GBNF.
- Dodano dwujęzyczny (PL/EN) prompt wyjaśniający zasady Szachów Eliminacyjnych: bicie króla wygrywa, samo szach-mat nie kończy partii, a odpowiedź ma zawierać dokładnie jeden ruch UCI.
- Ograniczenie gramatyczne UCI jest przekazywane zarówno przy pierwszej próbie, jak i przy ponowieniu odpowiedzi.
- W trybie ograniczonej gramatyki wyłączono tokeny rozumowania; ustawienie „thinking” może być zapisane jako żądane przez konfigurację modelu, ale rzeczywista inferencja ruchu działa bez tokenów rozumowania.
- Walidacja odpowiedzi wymaga pełnego dopasowania tekstu do pojedynczego ruchu UCI; tekst objaśniający lub dodatkowa treść nie są wycinane i akceptowane jako poprawny ruch.

## Przygotowane testy

Plik `tests/test_elimination_chess.py` obejmuje m.in.:

- legalność bicia króla i wynik partii po takim ruchu;
- dopuszczenie bicia króla także wtedy, gdy własny król pozostaje pod atakiem;
- kontynuowanie gry po pozycji szach-mat oraz brak automatycznego remisu przy samych królach;
- brak przedwczesnego zakończenia zwykłej pozycji;
- odtworzenie pozycji końcowej z FEN bez króla przeciwnika.

Zmieniono również testy runnera w `tests/test_engine_runner.py`, aby uwzględnić zmianę promptu i walidacji.

**Status weryfikacji (19.11.2026):** pełny zestaw testów repozytorium zakończył się wynikiem **465 passed, 59 subtests passed** (`python -m pytest -q`). Dodatkowo `git diff --check` nie zgłosił błędów. To potwierdza testy automatyczne w lokalnym worktree; nie zastępuje walidacji produkcyjnej.

## Testy modeli w runtime Areny

Testy wykonano sekwencyjnie w izolowanym kontenerze `arena-test`, na lokalnym `llamafile v0.10.6`, przez ten sam endpoint `/v1/chat/completions`, z takim samym polem `grammar` (GBNF), promptem PL/EN, `enable_thinking=false`, temperaturą 0, `max_tokens=16` dla ruchów, 2 wątkami CPU i kontekstem 2048. Pytania Passkey wysłano bez limitu czasu żądania, z limitem generacji 900 tokenów; zmierzono czas odpowiedzi.

| Model i dokładny plik testowy | Ruchy zgodne z GBNF | Bicie króla przy dostępnej możliwości | Czas ruchów (4 pozycje) | Passkey |
|---|---:|---:|---:|---|
| SmolLM2 360M Instruct Q2_K | 4/4 | **Tak, 1/1** (`e2e8`) | 32,67 / 20,54 / 5,98 / 12,96 s | 3 odpowiedzi słabe, powtarzalne; 139,43 / 149,96 / 160,08 s |
| Qwen2 0.5B Instruct Q4_K_M | 4/4 | Nie (wybrał `e2e7`) | 4,53 / 3,04 / 0,93 / 2,01 s | 3 odpowiedzi merytorycznie niewystarczające; 60,60 / 11,18 / 61,13 s |
| Qwen2.5 0.5B Instruct Q2_K | 4/4 | Nie (wybrał `e2e7`) | 32,13 / 20,69 / 6,97 / 13,72 s | 3 odpowiedzi błędne lub powtarzalne; 151,59 / 34,46 / 28,87 s |

Interpretacja wyników:

- Wynik **4/4** potwierdza przede wszystkim działanie ograniczenia GBNF i poprawne parsowanie pojedynczego UCI; sam w sobie nie dowodzi umiejętności gry ani siły szachowej.
- W teście priorytetu bicia króla tylko SmolLM2 wybrał dostępne natychmiastowe zwycięstwo. Dwa modele Qwen wybrały inne legalne ruchy.
- Żaden z trzech modeli nie zdał jakościowo trzech pytań Passkey: odpowiedzi zawierały błędy, powtarzanie treści lub brak odpowiedzi na sedno.
- Testowane pliki Qwen2.5 Q2_K i SmolLM2 Q2_K nie są tymi samymi kwantyzacjami co odpowiednie wpisy katalogu modeli Q4_K_M. Qwen2 Q4_K_M odpowiada wpisowi katalogowemu.
- W kontenerze były dostępne tylko te trzy pliki modeli. Pozostałych kandydatów nie pobierano, ponieważ wymagałoby to dodatkowego transferu modeli; nie zmieniono składu turnieju ani statusu żadnego zawodnika.

Wcześniejsze próby przez GPT4All 2.8.2 są wyłącznie wstępnym eksperymentem i nie są używane jako wynik porównawczy: nie korzystały z runtime Areny ani z ograniczenia GBNF. Wyniki powyżej pochodzą z właściwego runtime llamafile.

### Ponowny test po wzmocnieniu instrukcji o natychmiastowym biciu króla

Pierwszy pomiar wykazał, że dwa modele Qwen wybierały inny legalny ruch mimo dostępnego bicia króla. W `engine_runner.py` wzmocniono więc instrukcję systemową PL/EN i dodano do promptu osobną listę `KING-CAPTURE WIN MOVES`. Nie zawężono gramatyki do tych ruchów — pozostałe legalne ruchy nadal są dostępne, a wybór natychmiastowego zwycięstwa pozostaje decyzją modelu.

Powtórzono wyłącznie cztery testy szachowe, tym razem z rzeczywistą pozycją szach-mat (Fool's Mate) oraz z nowym promptem:

| Model | Poprawny UCI / GBNF | Ruch w pozycji szach-mat | Wybór bicia króla | Czas ruchów (4 pozycje) |
|---|---:|---|---|---|
| SmolLM2 360M Q2_K | 4/4 | `f1g2` | **Tak** (`e2e8`) | 40,21 / 20,20 / 15,60 / 18,51 s |
| Qwen2 0.5B Q4_K_M | 4/4 | `f1g2` | **Tak** (`e2e8`) | 5,66 / 3,08 / 2,61 / 2,86 s |
| Qwen2.5 0.5B Q2_K | 4/4 | `b1c3` | **Tak** (`e2e8`) | 37,08 / 20,30 / 16,77 / 18,41 s |

Wynik jest pozytywny dla testowanego zachowania promptu: **wszystkie trzy modele wybrały natychmiastowe bicie króla w pierwszej próbie**, a każdy zwrócił pojedynczy ruch dopuszczony przez GBNF. To nadal nie jest ocena siły szachowej ani test pełnej partii.

## Ograniczenia i dalsza weryfikacja

Ten zapis nie potwierdza jeszcze pełnej integracji wariantu z całym systemem. Przed zamknięciem Issue #188 należy zweryfikować co najmniej:

- wszystkie miejsca tworzenia i odtwarzania planszy, w tym wznowienia/checkpointy;
- pełny test end-to-end turnieju z modelem i odtworzeniem checkpointu w działającej usłudze;
- prezentację wyniku i wariantu w panelu WWW oraz eksport PGN z poziomu panelu.

Testy automatyczne obejmują już ruchy, SAN, PGN wariantu (także PGN awaryjny), FEN/checkpoint i walidację API. Nie zastępują jednak testu działającego turnieju ani przeglądu panelu.

Przeprowadzono testy trzech już dostępnych artefaktów modeli w izolowanym runtime. Nie zmieniono składu turnieju, zasad dopuszczania ani statusu żadnego zawodnika/modelu. Nie pobierano nowych modeli, nie wdrażano zmian na produkcję i nie restartowano usług produkcyjnych.

## Stan repozytorium w chwili zapisu

- Gałąź robocza: `feat/issue-188-elimination-engine-20261119`.
- Zmiany kodu wymienione wyżej były już obecne w tej gałęzi jako prace robocze.
- Ten commit dokumentuje ich zakres i status; nie jest potwierdzeniem kompletności implementacji.
- Wdrożenie i merge do `main` nie są częścią tego zapisu.
