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
- Dodatkowo 20.11 wykonano pełny, izolowany cykl serwera turniejowego z rzeczywistym Qwen2.5 i Qwen2 na tymczasowej bazie SQLite, z osobnym portem 18765: preflight → zapis checkpointu → przygotowanie zawodników → inferencja → nadpisanie propozycji e2e7 na wymuszone e2e8 → zapis partii/PGN → zamknięcie parowania i turnieju → usunięcie checkpointu. Turniej i parowanie zakończyły się statusem completed; wynik 1-0; runtime został zamknięty. Następnie wykonano pełny dwumeczowy round-robin dwóch zawodników (obie nogi, oba kolory): dwa parowania zakończyły się statusem completed, oba wyniki 1-0, oba PGN zawierają wariant i Rxe8, a checkpointy zostały usunięte.
- Panel WWW uruchomiono dodatkowo na izolowanym HTTP serverze z tymczasową bazą. Chromium headless wyrenderował stronę, informację o wariancie i dokładnie 64 pola planszy. Endpointy listy partii, szczegółów, pojedynczego PGN i eksportu PGN zwróciły poprawny wynik oraz nagłówek Elimination Chess i ruch Rxe8.
- Weryfikacja HTTP/Chromium była odizolowana; nie była testem przeglądarkowego panelu produkcyjnego. Nie należy traktować jej jako walidacji produkcyjnej.


### Dowód rzeczywistego cyklu turnieju i panelu — 20.11.2026

- W pełnym round-robin dwóch zawodników (Qwen2.5 0.5B Q2_K i Qwen2 0.5B Q4_K_M) rozegrano obie nogi na rzeczywistych modelach. Qwen2.5 zaproponował e2e7; runner zatwierdził e2e8 / Rxe8. Qwen2 sam wybrał e2e8. Zapis pierwszego ruchu zachował oba pola: model_proposed_uci=e2e7 oraz move_override=forced_king_capture.
- Tymczasowa baza potwierdziła: tournament_status=completed, oba parowania completed, dwa zapisane mecze 1-0, po jednym ruchu, oba PGN z wariantem i biciem króla, checkpoint_count_after_finish=0. Runtime nie miał załadowanego modelu po zakończeniu.
- Zarejestrowana sekwencja zdarzeń serwera obejmowała obie partie, zmianę ładowanego modelu między kolorami, move_generated, move_played, game_finished i końcowe completed.
- Chromium headless: HTTP 200, informacja „Szachy Eliminacyjne” obecna, 64 pola planszy wyrenderowane.
- Izolowane endpointy HTTP: /api/games, /api/games/1, /api/games/1/pgn i /api/export/pgn zwróciły oczekiwane dane, wariant PGN i Rxe8.
- Zbiorczy, zanonimizowany dowód JSON: docs/evidence/issue-188-real-e2e-20261120.json.
- Testy automatyczne po wymuszeniu natychmiastowego bicia króla: **471 passed, 59 subtests passed**. Nie zmieniano składu turnieju ani statusów zawodników.
- Żaden proces testowy nie używał produkcyjnego portu 8088/8090 ani produkcyjnej bazy. Porty testowe zostały zwolnione. Usługa produkcyjna pozostała aktywna i nietknięta.


### Blokada kryterium pięciu zatwierdzonych kandydatów

- Sprawdzono wersjonowaną allowlistę z gałęzi oraz z origin/main: zawiera 8 wpisów, wszystkie ze statusem review_required; brak wpisów eligible/restricted.
- Produkcyjna baza SQLite została odczytana wyłącznie w trybie read-only: 0 decyzji admission ze statusem admitted i 0 zdarzeń admitted w historii.
- W izolowanym kontenerze były dostępne trzy pliki GGUF; wszystkie trzy zostały przetestowane. Nie znaleziono pięciu zatwierdzonych kandydatów, których można bezpiecznie dopisać do wymaganej sekwencji testowej.
- Nie wybierano modeli z puli review_required ani kwarantanny, nie pobierano dodatkowych plików i nie zmieniano statusów admission. Kryterium pięciu zatwierdzonych kandydatów pozostaje otwarte do czasu jawnych decyzji admission / zatwierdzenia konkretnej listy przez właściciela.

### Decyzja właściciela — kandydat nr 2 (20.11.2026)

- Właściciel projektu zatwierdził **kandydata nr 2: Qwen2-0.5B-Instruct (Q4_K_M)** do dalszej weryfikacji w ramach Issue #188.
- Jest to model już obecny w izolowanym środowisku i wcześniej przetestowany w rzeczywistym runtime llamafile v0.10.6. W dwumeczu round-robin wykonał samodzielnie natychmiastowe bicie króla; w obu partiach zwrócił legalny ruch UCI.
- Zatwierdzenie dotyczy wyłącznie kontynuowania weryfikacji tego kandydata. **Nie jest decyzją admission, dopuszczeniem do turnieju ani zgodą na wdrożenie.**
- Nie pobrano nowego modelu, nie zmieniono allowlisty ani statusów admission, nie zmieniono składu turnieju i nie dotknięto produkcji.
- Pozostałe cztery miejsca wymagane do spełnienia kryterium pięciu zatwierdzonych kandydatów nadal pozostają nierozstrzygnięte.

### Przegląd pozostałych kandydatów z aktywnego manifestu (20.11.2026)

Poprzednia odpowiedź w rozmowie zawierała nieprawidłową shortlistę (m.in. Qwen2.5-1.5B i Spark X2.5), których nie ma w aktywnym manifeście. Nie należy traktować tej listy jako źródła decyzji. Poniższa ocena opiera się na manifests/huggingface-models.json i docs/model-audit/license-review-2026-11-14.md, bez pobierania wag.

| ID manifestu | Kandydat / plik GGUF | Rozmiar pliku | Audyt licencji | Ocena do dalszych testów |
|---:|---|---:|---|---|
| 1 | Qwen2.5-0.5B-Instruct Q4_K_M | 491 MB | verified_permissive | Najlepszy następny kandydat techniczno-licencyjny; w środowisku testowym badano już Q2_K, nie dokładny artefakt Q4_K_M z manifestu. Wymaga osobnej zgody właściciela na użycie artefaktu. |
| 2 | SmolLM2-360M-Instruct Q4_K_M | 271 MB | review_required | Technicznie mały; wcześniej badano Q2_K, nie dokładny Q4_K_M. Blokada licencyjna dotyczy nieprzypiętych snapshotów/subsetów danych treningowych i provenance The Stack. Nie rekomenduję testu przed zamknięciem tej luki lub świadomą decyzją właściciela. |
| 3 | Qwen2-0.5B-Instruct Q4_K_M | 398 MB | verified_permissive | Kandydat zatwierdzony przez właściciela do dalszej weryfikacji; exact Q4_K_M był już użyty w testach. |
| 4 | Meta Llama 3.1 8B Instruct Q3_K_S | 3,665 MB | review_required | Nie rekomenduję na obecnym środowisku bez preflight RAM; sam plik ma ok. 3,66 GB. Brak przypiętej rewizji upstream base modelu; obowiązują warunki Meta Llama Community License/AUP. |
| 5 | Llama 3.2 3B Instruct UD-IQ3_XXS | 1,370 MB | review_required | Potencjalnie jedyny większy kandydat do późniejszego preflight, ale nie przed uzupełnieniem przypiętej rewizji upstream i przeglądem warunków Meta. Nie uruchamiać ani nie pobierać bez osobnej zgody. |
| 6 | Bielik 11B v3.0 Instruct Q4_K_M | 6,724 MB | verified_restricted | Wykluczony z proponowanej ścieżki: oficjalne warunki Bielik v3 dopuszczają użycie wyłącznie osobom 18+; plik jest też zbyt duży na obecny profil testowy. |
| 7 | Bielik 7B Instruct v0.1 Q3_K_S | 3,165 MB | verified_restricted | CC BY-NC 4.0 (NonCommercial + atrybucja/oznaczenie zmian); plik jest duży względem środowiska. Nie rekomenduję jako kandydata do tego testu. |
| 8 | Qwen3.8-27B UD-IQ1_S | 6,192 MB | review_required | Nieodpowiedni do obecnego środowiska; dodatkowe pliki imatrix/mmproj w repozytorium pochodnym nie mają zamkniętego audytu komponentów. |

Wnioski operacyjne:
- Jedynymi małymi kandydatami z aktywnego manifestu, które nie mają blokady licencyjnej na poziomie audytu, są Qwen2.5-0.5B (ID 1) i Qwen2-0.5B (ID 3). Qwen2 jest już zatwierdzony przez właściciela.
- SmolLM2 (ID 2) ma niewielki artefakt, ale jego audyt pozostaje otwarty z konkretnych powodów dotyczących źródeł treningowych.
- Llama 3.2 3B (ID 5) może być rozważany dopiero po uzupełnieniu proweniencji i formalnym sprawdzeniu warunków licencji; nie zakładać, że rozmiar pliku oznacza możliwość bezpiecznego uruchomienia.
- Nie ma obecnie pięciu kandydatów jednocześnie: zatwierdzonych przez właściciela, z zamkniętymi wymaganiami licencyjnymi i odpowiednich do bezpiecznego testu na istniejącym środowisku. Nie należy sztucznie uzupełniać listy modelami 7B/8B/11B/27B.
- Ta ocena nie zmienia statusu manifestu ani admission, nie stanowi zgody na pobranie/uruchomienie, nie zmienia rosteru i nie dotyka produkcji.
- Następny sensowny krok po osobnej zgodzie właściciela: przygotować Qwen2.5-0.5B Q4_K_M jako kandydata do izolowanego preflight (bez pobierania); równolegle można kontynuować dokumentacyjny przegląd brakujących danych licencyjnych SmolLM2 i Llama 3.2.



### Preflight zasobów dla Qwen2.5-0.5B-Instruct Q4_K_M (20.11.2026)

- Na hoście odnaleziono właściwą maszynę `arena-test` uruchomioną przez `systemd-nspawn` (Debian 13). Wewnątrz dostępny jest `llamafile v0.10.6`.
- Odczyt zasobów hosta: RAM 3,7 GiB łącznie, ok. 1,4 GiB dostępne; swap 2 GiB, ok. 0,9 GiB wolne; wolne miejsce na systemowym filesystemie ok. 14 GiB.
- Konfiguracja jednostki `systemd-nspawn@arena-test.service` raportuje `MemoryMax=1452213043` B (ok. 1,35 GiB) i `CPUQuotaPerSecUSec=1.5` (limit 1,5 CPU). Są to limity konfiguracyjne jednostki, nie pomiar chwilowego zużycia modelu.
- W katalogu modeli istnieją już artefakty SmolLM2 Q2_K, Qwen2 Q4_K_M oraz Qwen2.5 Q2_K. **Dokładny Qwen2.5-0.5B-Instruct Q4_K_M z manifestu (491 400 032 B, SHA-256 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`) nie jest obecny.**
- Wynik: `BLOCKED_NOT_RUN`. Weryfikacja dotyczyła wyłącznie zasobów i obecności artefaktu; nie uruchamiano Q4_K_M, nie pobierano go, nie zmieniano konfiguracji ani procesów środowiska.
- Dostępna pamięć hosta i limit pamięci jednostki nie dają wystarczającego marginesu, aby uznać próbę za bezpieczną bez dodatkowego pomiaru/planowania pamięci. Nie rekomenduje się ładowania tego modelu na obecnym profilu bez osobnej decyzji i przygotowania bezpiecznego budżetu RAM.
- Produkcyjna usługa `ai-chess-arena.service`, baza produkcyjna, panel, roster i statusy admission pozostały nietknięte. Nie wykonano wdrożenia ani restartu usług.


### Stały budżet zasobów i ponowny odczyt `arena-test` — 20.11.2026

Ustalono, że budżet sprzętowy nie będzie zwiększany. Dalsze prace i dobór modeli muszą mieścić się w istniejącym środowisku; nie zmieniamy RAM, swapu, limitów CPU ani konfiguracji kontenera.

Odczyt hosta `vps-678a1be0`:

- RAM: 3,7 GiB łącznie, około 1,4 GiB dostępne w chwili pomiaru.
- Swap: 2,0 GiB łącznie, około 894 MiB wolne.
- Dysk `/`: 40 GiB łącznie, 14 GiB wolne.
- `arena-test` działa jako transient scope `machine-arena\\x2dtest.scope`; bieżące zużycie pamięci około 82 MiB, historyczny szczyt scope około 1,04 GiB.
- Efektywne limity bieżącego scope: `memory.max=max`, `memory.high=max`, `memory.swap.max=max`, `cpu.max=max 100000`.
- Jednostka `systemd-nspawn@arena-test.service` jest obecnie `inactive`. Jej drop-iny deklarują `MemoryMax=1452213043` B (około 1,35 GiB) i `CPUQuota=150%`, ale nie są efektywnymi limitami aktualnie działającego transient scope. Nie wolno traktować ich jako aktywnego limitu kontenera.

Ponowny odczyt katalogu modeli w maszynie potwierdził dokładnie trzy istniejące pliki:

| Artefakt | Rozmiar |
|---|---:|
| `SmolLM2-360M-Instruct.Q2_K.gguf` | 218 673 792 B |
| `qwen2-0_5b-instruct-q4_k_m.gguf` | 397 805 248 B |
| `qwen2.5-0.5b-instruct-q2_k.gguf` | 415 182 688 B |

Nie znaleziono dodatkowego, niewykorzystanego artefaktu GGUF. Qwen2 Q4_K_M ma status `verified_permissive` i został wcześniej zatwierdzony przez właściciela do dalszej weryfikacji, ale nie zdał jakościowo testów Passkey. Qwen2.5 Q4_K_M (491 400 032 B) nie jest obecny na dysku; SmolLM2 Q4_K_M (271 MB) pozostaje `review_required` z powodu nierozstrzygniętej proweniencji danych treningowych. Żadnego z tych plików nie pobierano.

Wniosek operacyjny: nie ma obecnie kolejnego lokalnego kandydata do testu. Kontynuować można wyłącznie pracę nad promptami, walidacją i testami istniejących artefaktów, bez rozszerzania rosteru, bez zmiany admission, bez pobierania modeli i bez zmian produkcyjnych. Ten odczyt był read-only; nie zmieniano jednostek systemd, cgroup, kontenera, usług ani plików modeli.


### Czytelniejszy prompt odpowiedzi Passkey — 20.11.2026

Wprowadzono niewielką zmianę promptu w `passkey_test.py`; nie jest to fine-tuning ani trening wag modelu. Każde pytanie Passkey otrzymuje teraz osobną wiadomość systemową, która instruuje model, aby:

- odpowiadał po polsku, chyba że pytanie wymaga innego języka;
- zaczynał od bezpośredniej odpowiedzi i dopiero potem krótko ją uzasadniał;
- używał prostego, precyzyjnego języka i objaśniał skróty przy pierwszym użyciu;
- stosował krótkie akapity lub listę kroków przy opisie procedury;
- oddzielał fakty od założeń i nie wymyślał ustawień ani zachowania przeglądarek;
- jawnie wskazywał niepewność, zamiast zgadywać;
- odpowiadał na wszystkie części pytania bez dygresji oraz wskazywał główne ryzyko i zabezpieczenie w tematach bezpieczeństwa.

Dodano test sprawdzający, czy wiadomość systemowa jest przekazywana do API przed pytaniem użytkownika. Lokalna kontrola składni oraz test żądania z atrapą odpowiedzi przeszły poprawnie. Nie jest to jednak pełny test zestawu repozytorium ani test rzeczywistych modeli.

**Status testu A/B na modelach: NOT_RUN.** Odczyt środowiska wykazał, że na porcie 8088 nie działa runtime llamafile i nie ma uruchomionego procesu inferencji. Próba wejścia do maszyny `arena-test` przez `machinectl shell` zakończyła się komunikatem `Interactive authentication required`, a katalog maszyny jest niedostępny do odczytu bez uprawnień roota. Nie uruchamiałem modelu ani nie startowałem nowej usługi w tej sesji. Trzy istniejące modele pozostają jedynymi kandydatami do kolejnego testu; należy porównać odpowiedzi na tych samych trzech pytaniach przed i po zmianie promptu, sekwencyjnie, z zapisem pełnych odpowiedzi, czasu i oceny według wspólnej rubryki (poprawność techniczna, kompletność, zrozumiałość, brak zmyśleń). Nie zmieniać pytań w trakcie porównania.

Zmiana nie wpływa na prompty ruchów szachowych, zasady wariantu, roster, admission ani produkcję. Nie pobrano modeli i nie zmieniano limitów zasobów.
