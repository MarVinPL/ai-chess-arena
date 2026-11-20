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

**Status weryfikacji (19.11.2026):** pełny zestaw testów repozytorium zakończył się wynikiem **468 passed, 59 subtests passed** (`python -m pytest -q`). Dodatkowo `git diff --check` nie zgłosił błędów. To potwierdza testy automatyczne w lokalnym worktree; nie zastępuje walidacji produkcyjnej.

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
- pełny test end-to-end turnieju z modelami w działającej usłudze, w tym prezentację wyniku na uruchomionym panelu i eksport PGN z poziomu przeglądarki.

Testy automatyczne obejmują już ruchy, SAN, PGN wariantu (także PGN awaryjny), FEN/checkpoint i walidację API. Dodano także test wznowienia runnera z odtworzoną historią wariantu, po którym model wykonuje bicie króla i kończy partię. Panel WWW otrzymał widoczną informację o zasadach wariantu nad planszą; test UI sprawdza obecność tej informacji. To przegląd kodu i testów źródłowych, nie test uruchomionego panelu ani pełnego turnieju w działającej usłudze.

Przeprowadzono testy trzech już dostępnych artefaktów modeli w izolowanym runtime. Nie zmieniono składu turnieju, zasad dopuszczania ani statusu żadnego zawodnika/modelu. Nie pobierano nowych modeli, nie wdrażano zmian na produkcję i nie restartowano usług produkcyjnych.

## Stan repozytorium w chwili zapisu

- Gałąź robocza: `feat/issue-188-elimination-engine-20261119`.
- Gałąź zawiera implementację wariantu, test wznowienia po checkpointcie oraz widoczną informację o zasadach nad planszą.
- Zakres pozostaje niekompletny do czasu przejścia pełnego testu turnieju w działającej usłudze.
- Wdrożenie i merge do `main` nie są częścią tego zapisu.


## Aktualizacja po dalszej weryfikacji (19.11.2026)

- Panel WWW pokazuje teraz nad planszą informację: „Szachy Eliminacyjne” oraz krótkie wyjaśnienie, że zwycięstwo następuje po zbiciu króla, a szach/szach-mat nie kończą partii ani nie blokują ruchów.
- Dodano test UI potwierdzający obecność i treść tej informacji.
- Dodano test runnera, który odtwarza dwuruchową historię checkpointu na EliminationBoard, weryfikuje zgodność FEN, wykonuje bicie króla po wznowieniu i sprawdza wynik oraz nagłówek wariantu w PGN.
- Dodano integracyjny test całej pętli turnieju serwera na tymczasowej bazie: preflight, zapis checkpointu, przygotowanie zawodników, runner, bicie króla, zapis wyniku/PGN, zakończenie parowania i usunięcie checkpointu. Runtime inferencji jest w tym teście deterministycznym zamiennikiem, a nie rzeczywistym modelem.
- Pełny zestaw: **468 passed, 59 subtests passed**; compileall i git diff --check bez błędów.
- Nie udało się jeszcze wykonać pełnego turnieju z rzeczywistymi modelami przez działającą usługę Areny: w izolowanym kontenerze arena-test obecnie nie działa usługa serwera Areny (widoczne są tylko podstawowe usługi systemowe). Istniejący /opt/arena-preview to odrębny, wcześniejszy podgląd testowy oparty na standardowych szachach i nie został zmodyfikowany ani użyty jako dowód działania nowego runnera. Produkcja pozostaje nietknięta.


## Aktualizacja po testach z rzeczywistymi modelami i egzekwowaniu reguły (20.11.2026)

- Ponownie uruchomiono aktualną gałąź na istniejących artefaktach w izolowanym kontenerze arena-test, z llamafile v0.10.6, 2 wątkami CPU i kontekstem 2048. Nie pobierano modeli.
- W pozycji z dostępnym natychmiastowym biciem króla surowe odpowiedzi modeli były następujące:
  - SmolLM2 360M Q2_K: e2e8 / Rxe8, król zbity, wynik 1-0 (41,14 s).
  - Qwen2 0.5B Q4_K_M: e2e8 / Rxe8, król zbity, wynik 1-0 (6,62 s).
  - Qwen2.5 0.5B Q2_K: e2e7 / Re7+, król niezbity, brak wyniku końcowego (35,90 s).
- Powtórny test pozycji po Fool's Mate potwierdził, że Qwen2 wybrał legalny ruch f1g2, a partia pozostała aktywna (game_over=false, 19 legalnych ruchów przed odpowiedzią).
- Wynik Qwen2.5 dowiódł, że nawet mocniejszy prompt nie gwarantuje wymuszenia natychmiastowego zwycięstwa. Dlatego regułę przeniesiono z samej instrukcji modelu do egzekwowania w logice runnera: model nadal generuje propozycję, ale jeśli dostępne jest legalne bicie króla i model go nie wybierze, runner zatwierdza pierwsze dostępne bicie króla. Propozycja modelu i powód nadpisania są zapisywane w rekordzie ruchu (model_proposed_uci, move_override, move_override_reason).
- Tę samą regułę zastosowano w legacy server.ask_model, aby pojedyncza partia i runner turniejowy zachowywały się identycznie.
- Potwierdzenie na rzeczywistym Qwen2.5 w SequentialChessRunner: model zaproponował e2e7, runner zatwierdził e2e8 / Rxe8, partia zakończyła się wynikiem 1-0, terminacją VARIANT_LOSS, a PGN zawiera nagłówek wariantu. Czas inferencji: 35,46 s.
- Dodano testy jednostkowe dla listy ruchów wygrywających, nadpisania propozycji modelu w runnerze oraz legacy ask_model.
- Pełny zestaw testów po zmianach: **471 passed, 59 subtests passed**. Dodatkowo compileall i git diff --check bez błędów.
- Usługa produkcyjna ai-chess-arena.service została sprawdzona wyłącznie odczytowo. Działa z osobnego katalogu wydania ai-chess-arena-live-ab4a8cb; nie została zatrzymana, zrestartowana ani zmodyfikowana. Testy uruchomiono w izolowanym kontenerze, a produkcyjna baza, panel i skład turnieju pozostały nietknięte.
- Dodatkowo 20.11 wykonano pełny, izolowany cykl serwera turniejowego z rzeczywistym Qwen2.5 i Qwen2 na tymczasowej bazie SQLite, z osobnym portem 18765: preflight → zapis checkpointu → przygotowanie zawodników → inferencja → nadpisanie propozycji e2e7 na wymuszone e2e8 → zapis partii/PGN → zamknięcie parowania i turnieju → usunięcie checkpointu. Turniej i parowanie zakończyły się statusem completed; wynik 1-0; runtime został zamknięty. Test obejmował jedno parowanie dwóch zawodników, nie pełny wieloparowy turniej.
- Panel WWW uruchomiono dodatkowo na izolowanym HTTP serverze z tymczasową bazą. Chromium headless wyrenderował stronę, informację o wariancie i dokładnie 64 pola planszy. Endpointy listy partii, szczegółów, pojedynczego PGN i eksportu PGN zwróciły poprawny wynik oraz nagłówek Elimination Chess i ruch Rxe8.
- Weryfikacja HTTP/Chromium była odizolowana; nie była testem przeglądarkowego panelu produkcyjnego. Nie należy traktować jej jako walidacji produkcyjnej.


### Dowód rzeczywistego cyklu turnieju i panelu — 20.11.2026

- Rzeczywisty model Qwen2.5 0.5B Q2_K w pętli serwera zaproponował e2e7; runner zatwierdził e2e8 / Rxe8. Zapis ruchu zachował oba pola: model_proposed_uci=e2e7 oraz move_override=forced_king_capture.
- Tymczasowa baza potwierdziła: tournament_status=completed, pairing_status=completed, result=1-0, move_count=1, PGN z wariantem i biciem króla, checkpoint_count_after_finish=0. Runtime nie miał załadowanego modelu po zakończeniu.
- Zarejestrowana sekwencja zdarzeń serwera obejmowała starting, game_prepare, game_ready, game_running, model_loading, model_ready, generating, move_generated, move_played, game_finished i completed.
- Chromium headless: HTTP 200, informacja „Szachy Eliminacyjne” obecna, 64 pola planszy wyrenderowane.
- Izolowane endpointy HTTP: /api/games, /api/games/1, /api/games/1/pgn i /api/export/pgn zwróciły oczekiwane dane, wariant PGN i Rxe8.
- Zbiorczy, zanonimizowany dowód JSON: docs/evidence/issue-188-real-e2e-20261120.json.
- Testy automatyczne po wymuszeniu natychmiastowego bicia króla: **471 passed, 59 subtests passed**. Nie zmieniano składu turnieju ani statusów zawodników.
- Żaden proces testowy nie używał produkcyjnego portu 8088/8090 ani produkcyjnej bazy. Porty testowe zostały zwolnione. Usługa produkcyjna pozostała aktywna i nietknięta.
