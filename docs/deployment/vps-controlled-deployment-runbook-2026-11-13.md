# Kontrolowany runbook wdrożenia AI Chess Arena na VPS

Stan audytu: 2026-11-13. Dokument jest planem, nie zgodą na deploy ani restart.

## Decyzja na podstawie bieżącego stanu

**NO-GO dla wdrożenia i restartu na tym etapie.** Kod docelowy jest gotowy w GitHub `main`, ale obecny proces API nie ma własnej jednostki systemd. `ai-chess-arena.service` zwraca `LoadState=not-found`; proces API (obserwowany PID 2943842) należy do cgroup aktywnej `sentinelx-cloud-core.service`. Restart tej usługi mógłby przerwać również narzędzia zdalnego dostępu. Nie wolno używać jej jako mechanizmu restartu Areny.

Przed wdrożeniem potrzebny jest osobny, zatwierdzony mechanizm zarządzania procesem Areny, niezależny od SentinelX, wraz z rollbackiem. Ten runbook nie tworzy ani nie uruchamia takiej usługi.

## Porównanie źródeł

| Element | Stan |
|---|---|
| GitHub `main` – docelowy commit | `b5ebf13949db43212c4369667b8d90a47cdf492c` |
| Checkout na VPS – HEAD | `52bd27d0b4da0bef48c61e2ba78edf1b141df9e5` |
| Różnica | VPS ma 9 commitów za `main` |
| Zmiany w docelowym `main` względem VPS HEAD | 10 plików: 1057 insertions, 96 deletions |
| Zależności | `requirements.txt` i `requirements-dev.txt` nie zmieniły się |
| Backend API | `127.0.0.1:8090`, GET-y do state/live/tournaments/models/storage odpowiadają HTTP 200 |
| Llamafile API | `127.0.0.1:8088/health` nie odpowiada podczas audytu |
| Bieżący stan aplikacji | `running=false`, `active_model=null`, `llm_online=false`; ostatni błąd turnieju dotyczy UCI `g6d8` |
| Turnieje | #4 = failed; #5 = planned, 6 oczekujących parowań |
| DB | 10 wersji modeli, 9 zapisanych gier |

Docelowy `main` zawiera m.in. poprawki planszy (#12), antyoscylację (#13), kwalifikacje i draft (#14), ocenę przewagi materiałowej (#16), tożsamość konfiguracji zawodnika (#18), zasady repo (#20), adaptacyjny limit tokenów (#22), dokumentację procesu (#27) i diagnostykę prób UCI (#28).

### Lokalne pliki, których nie wolno nadpisywać bez zabezpieczenia

W katalogu `/home/debian/ai-chess-arena` wykryto:
- zmodyfikowany względem lokalnego HEAD plik `index.html`, SHA-256 `950274174649f25dc57ae5ab05d5d877be7eadd5533f9cfe03ae4dd2d16e0cd0`;
- nieśledzony backup `index.html.before-board-arrows-20261113-185919`;
- istniejące starsze kopie `index.html.bak-20261112T231822Z`, `index.html.bak-20261112T233649Z`, `server.py.bak-20261112T231822Z`, `server.py.bak-20261112T233649Z`.

Live `index.html` nie jest identyczny z docelowym plikiem na `main`. Docelowa wersja zawiera nowsze funkcje UI (m.in. konfiguracje myślenia, ustawienia zawodnika, przewagę materiałową). Wdrożenie musi zachować dokładną kopię obecnego pliku i backupów na potrzeby rollbacku. Nie wykonywać `git pull` w obecnym, zmodyfikowanym checkoutcie i nie usuwać żadnego backupu.

## Zmiany schematu SQLite

Baza: `/home/debian/ai-chess-arena/arena.sqlite3`.

Odczyt kontrolny:
- `PRAGMA integrity_check` = `ok`;
- `PRAGMA foreign_key_check` = brak błędów;
- 30 odpowiedzi Passkey, 0 z pustym `question_fingerprint`;
- obecne tabele: `games`, `league_models`, `model_versions`, `passkey_test_answers`, `tournament_entries`, `tournament_pairings`, `tournaments`.

Docelowe `league.init()` wykonuje przy starcie aplikacji migracje i tworzy brakujące struktury. W aktualnej bazie brakuje:
- kolumny `tournaments.qualification_percent`;
- kolumn `tournament_pairings.stage`, `round_no`, `match_no`, `winner_model_id`;
- tabeli `tournament_seeds`;
- tabel `token_budget_profiles`, `tournament_entry_budgets`, `token_budget_observations` oraz indeksu obserwacji.

Kolumny `games.tournament_id`, `pairing_id`, `move_times_json`, `inference_metadata_json`, `passkey_test_answers.question_fingerprint` oraz kolumny draft w `model_versions` są już obecne. Kod startowy ma także backfill pustych `question_fingerprint`; audyt wykazał obecnie 0 pustych wartości.

Migracje są zasadniczo addytywne, ale zmieniają produkcyjną bazę przy starcie procesu. Muszą być najpierw sprawdzone na kopii DB. Nie uruchamiać `db_init()` na produkcyjnej bazie bez uprzedniego, zweryfikowanego backupu i osobnej zgody na deploy.

## Procedura przed wdrożeniem

1. Potwierdzić dokładny SHA docelowego `main` i sprawdzić, czy nie pojawiły się nowsze commity.
2. Wykonać read-only preflight: procesy/porty, stan API, `/api/state`, `/api/live`, `/api/tournaments`, `/api/models`, `/api/storage`, stan Llamafile i status turniejów.
3. Potwierdzić, że żaden turniej nie jest `running`. Nie uruchamiać ani nie modyfikować #5; ma pozostać `planned`.
4. Zabezpieczyć dokładny bieżący `index.html`, backupy i stan checkoutu. Nie nadpisywać lokalnych plików.
5. Wykonać spójny backup SQLite przez API `sqlite3.Connection.backup()` (nie zwykłe kopiowanie aktywnego pliku DB). Zweryfikować kopię przez `PRAGMA integrity_check`, `PRAGMA foreign_key_check` i SHA-256.
6. Wykonać archiwum kodu/konfiguracji potrzebnej do rollbacku. Wykluczyć środowisko `.venv` i binaria modeli; nie dotykać `/home/debian/ai-agent/models`.
7. Przygotować czysty katalog stagingowy z dokładnego SHA `main` (osobny worktree/checkout). Nie aktualizować istniejącego katalogu przez `git pull`.
8. Na kopii DB uruchomić migracje z nowym kodem i porównać schemat oraz dane: zachować 5 turniejów, 6 pending parowań turnieju #5, roster i wyniki; potwierdzić nowe tabele/kolumny.
9. W stagingu uruchomić pełny zestaw testów, `py_compile`, kontrolę składni JS i smoke testy HTTP.
10. Przed przejściem do wdrożenia wymagana jest osobna zgoda użytkownika na deploy/restart oraz działający, dedykowany supervisor Areny. Nie restartować `sentinelx-cloud-core.service`.

## Wdrożenie – dopiero po osobnej zgodzie

- Wdrożyć wyłącznie sprawdzony commit z `main`, bez zmian w rosterze, DB poza migracjami przewidzianymi w kodzie ani w plikach modeli.
- Uruchomić backend przez dedykowaną usługę Areny, nie przez SentinelX.
- Nie uruchamiać automatycznie turnieju #5.
- Nie pobierać modeli ani nie instalować zależności, jeśli preflight nie wykaże konieczności; obecne pliki requirements są bez zmian.

## Smoke test po wdrożeniu

1. Sprawdzić status dedykowanej usługi i PID procesu API.
2. Potwierdzić HTTP 200 i poprawny JSON dla `/api/state`, `/api/live`, `/api/tournaments`, `/api/models`, `/api/storage`.
3. Zweryfikować `PRAGMA integrity_check` i obecność nowych tabel/kolumn.
4. Potwierdzić, że turniej #5 nadal ma status `planned`, ten sam roster i 6 pending parowań.
5. Sprawdzić status Llamafile `/health` dopiero po kontrolowanym uruchomieniu modelu; obecnie endpoint 8088 jest offline, więc nie można uznać testu inferencji za zaliczony.
6. Wykonać wyłącznie testy niezmieniające danych turniejowych; nie rozpoczynać produkcyjnej partii jako testu wdrożenia.

## Rollback

- Wykonywać wyłącznie po osobnej zgodzie i przez dedykowaną usługę Areny.
- Zatrzymać wyłącznie proces Areny (nigdy usługę SentinelX).
- Przywrócić archiwum kodu oraz spójny backup SQLite sprzed migracji.
- Zachować kopię nieudanego wdrożenia do analizy; nie kasować backupów.
- Ponownie sprawdzić integralność DB, API, listę turniejów, status #5 i health-check.
- Jeżeli nie ma pewności, czy baza lub kod zostały przywrócone w całości, pozostawić Arenę zatrzymaną i nie uruchamiać turnieju.

## Zakres wykonanych prac

Ten audyt był read-only. Nie wykonano backupu produkcyjnego, migracji DB, synchronizacji kodu, instalacji, deployu, restartu ani zmian w modelach/turniejach. Nie zmieniono istniejących lokalnych plików ani backupów.
