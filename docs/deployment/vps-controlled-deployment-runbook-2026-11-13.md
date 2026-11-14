# Kontrolowany runbook wdrożenia AI Chess Arena na VPS

Stan operacyjny: 2026-11-14. Wdrożenie kontrolowane zostało wykonane; poniżej zapisano faktyczny stan hosta i zachowane granice bezpieczeństwa.

## Decyzja na podstawie bieżącego stanu

**Wdrożenie wykonane 2026-11-14.** API działa pod dedykowaną jednostką `ai-chess-arena.service`, niezależną od SentinelX. Jednostka jest aktywna, ale pozostaje `disabled` (nie włączono startu przy bootowaniu — wymaga to osobnej zgody). Nie restartowano ani nie zatrzymywano `sentinelx-cloud-core.service`. Poprzedni proces Areny PID 2943842 zakończono pojedynczym SIGTERM po sprawdzeniu dokładnej komendy, katalogu, właściciela, cgroup i bezczynności API; nowy proces działa w `/system.slice/ai-chess-arena.service`.

Usługa używa `ProtectHome=read-only` oraz `BindPaths=/home/debian/ai-chess-arena`, co daje zapis tylko w katalogu Areny. `/home/debian/ai-agent/models` jest ukryty przez `InaccessiblePaths`; binarium Llamafile jest read-only. Próba zapisu do katalogu Areny i próba odczytu współdzielonego katalogu modeli zostały sprawdzone w tymczasowej jednostce systemd.

## Porównanie źródeł

| Element | Stan |
|---|---|
| GitHub `main` – wdrożony commit | `366b97d85395a9d1b4790326f5086131c9884cf4` |
| Checkout metadanych Git w katalogu live | `52bd27d0b4da0bef48c61e2ba78edf1b141df9e5` (nie wykonano `git pull`; kod wdrożono z czystego worktree) |
| Kod aplikacji | zsynchronizowany z `main` `366b97d`; lokalny `index.html` celowo zachowany |
| Zakres wdrożenia | pliki aplikacji z czystego worktree `main`, z wyłączeniem lokalnego `index.html` |
| Zależności | `requirements.txt` i `requirements-dev.txt` nie zmieniły się |
| Backend API | `127.0.0.1:8090`, GET-y do state/live/tournaments/models/storage odpowiadają HTTP 200 |
| Llamafile API | `127.0.0.1:8088/health` nie odpowiada podczas audytu |
| Bieżący stan aplikacji | `running=false`, `tournament_running=false`, `active_model=null`, `llm_online=false`, `live.stage=idle` |
| Turnieje | #4 = failed; #5 = failed, 3 completed + 3 pending; nie wznawiano |
| DB | 10 wersji modeli, 12 zapisanych gier; `integrity_check=ok`, `foreign_key_check` bez błędów |

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

## Gotowość modeli w aktualnym main

Aktualny kod Areny uruchamia wyłącznie wersje, które mają status `downloaded`, `managed_copy=1`, kompletny manifest filename/SHA-256/size i zweryfikowany plik we własnym katalogu `managed-models` (patrz `server.py::_version_path`).

Stan bazy z audytu:
- wszystkie 10 wersji ma `status=catalogued` i `managed_copy=0`;
- katalog `/home/debian/ai-chess-arena/managed-models` istnieje, ale jest pusty;
- wersje 1–3 mają manifest SHA/rozmiaru, ale nie mają lokalnej kopii zarządzanej przez Arenę;
- wersje 4–10 nie mają nawet kompletnego manifestu artefaktu.

Wniosek: **żadna z 10 wersji nie jest obecnie gotowa do uruchomienia przez aktualny main**, także modele historycznie używane w starszym runtime. Kompletność manifestu nie jest równoznaczna z obecnością i gotowością pliku.

Przed uruchomieniem przyszłego turnieju trzeba osobno zatwierdzić pobranie wybranych modeli do prywatnego `managed-models`. Ta operacja zapisuje duże pliki i aktualizuje status/manifest w DB; wymaga osobnej zgody, kontroli wolnego miejsca, źródła, licencji, SHA-256, rozmiaru i zgodności runtime. Nie jest częścią zwykłego deployu kodu. Nie używać ani nie modyfikować `/home/debian/ai-agent/models`; aktualny kod Areny odrzuca pliki spoza własnego katalogu.

## Procedura przed wdrożeniem

1. Potwierdzić dokładny SHA docelowego `main` i sprawdzić, czy nie pojawiły się nowsze commity.
2. Wykonać read-only preflight: procesy/porty, stan API, `/api/state`, `/api/live`, `/api/tournaments`, `/api/models`, `/api/storage`, stan Llamafile i status turniejów.
3. Potwierdzić, że żaden turniej nie jest `running`. Nie uruchamiać ani nie modyfikować #4 lub #5; ich statusy i parowania mają zostać zachowane.
4. Zabezpieczyć dokładny bieżący `index.html`, backupy i stan checkoutu. Nie nadpisywać lokalnych plików.
5. Wykonać spójny backup SQLite przez API `sqlite3.Connection.backup()` (nie zwykłe kopiowanie aktywnego pliku DB). Zweryfikować kopię przez `PRAGMA integrity_check`, `PRAGMA foreign_key_check` i SHA-256.
6. Wykonać archiwum kodu/konfiguracji potrzebnej do rollbacku. Wykluczyć środowisko `.venv` i binaria modeli; nie dotykać `/home/debian/ai-agent/models`.
7. Przygotować czysty katalog stagingowy z dokładnego SHA `main` (osobny worktree/checkout). Nie aktualizować istniejącego katalogu przez `git pull`.
8. Na kopii DB uruchomiono migracje z nowym kodem. Zachowano 5 turniejów, 12 gier, statusy #4/#5 oraz 3 zakończone i 3 oczekujące parowania #5; dodano wymagane tabele i kolumny.
9. W stagingu uruchomić pełny zestaw testów, `py_compile`, kontrolę składni JS i smoke testy HTTP.
10. Deploy/restart wykonano po osobnej zgodzie użytkownika, po przygotowaniu dedykowanego supervisora. Nie restartowano `sentinelx-cloud-core.service`.

## Wdrożenie wykonane 2026-11-14

- Kod backendu i pliki projektu zsynchronizowano z `main` commit `366b97d85395a9d1b4790326f5086131c9884cf4` z czystego worktree; nie wykonywano `git pull` w zabrudzonym checkoutcie.
- Lokalny `index.html` nie został nadpisany: SHA-256 `950274174649f25dc57ae5ab05d5d877be7eadd5533f9cfe03ae4dd2d16e0cd0`. Zachowano też `index.html.before-board-arrows-20261113-185919` i starsze kopie.
- Spójny backup SQLite: `/home/debian/ai-chess-arena/backups/pre-deploy-20261114-0722/arena.sqlite3`, SHA-256 `826f786d19f8940c5b0a76dc4a372804686bf0563c323a920db163ebe6fe352a`. Backup przeszedł `integrity_check=ok` i `foreign_key_check` bez błędów.
- Backup kodu przed synchronizacją: `/home/debian/ai-chess-arena/backups/pre-deploy-20261114-0722/app-code-before-deploy.tar.gz`, SHA-256 `a439a9f448e69438d250e1578af1c6b32a3669ed75abaaeffc2ce01214adc8cb`.
- Migracje przetestowano najpierw na kopii DB, a następnie uruchomił je backend przy starcie. Integralność produkcyjnej DB po migracji pozostała `ok`; brak błędów FK.
- `ai-chess-arena.service`: `active/running`, MainPID `3056142`, cgroup `/system.slice/ai-chess-arena.service`; `UnitFileState=disabled`.
- `sentinelx-cloud-core.service`: `active`, MainPID `748493` — bez restartu i bez zmiany PID.
- Nie instalowano zależności, nie pobierano modeli, nie zmieniano plików modeli ani współdzielonego katalogu modeli.
- Turnieje #4 i #5 pozostały `failed`; 12 gier zachowanych; #5 ma 3 parowania `completed` i 3 `pending`. Nie uruchamiano ani nie wznawiano turnieju.
- Llamafile pozostaje offline (`llm_online=false`); nie wykonano testu inferencji na żywym modelu.

## Wyniki smoke testu po wdrożeniu

1. `ai-chess-arena.service` jest `active/running`; PID API należy do `/system.slice/ai-chess-arena.service`.
2. `/api/state`, `/api/live`, `/api/tournaments`, `/api/models` i `/api/storage` zwróciły HTTP 200.
3. `PRAGMA integrity_check=ok`; `PRAGMA foreign_key_check` nie zwrócił błędów.
4. API raportuje `running=false`, `tournament_running=false`, `games_saved=12`, `llm_online=false`, `live.stage=idle`.
5. Turnieje #4 i #5 pozostały `failed`; #5 zachował 3 parowania `completed` i 3 `pending`.
6. Nie wykonano testu inferencji, ponieważ Llamafile jest offline. Nie uruchamiano partii ani turnieju jako testu wdrożenia.

## Rollback

- Wykonywać wyłącznie po osobnej zgodzie i przez dedykowaną usługę Areny.
- Zatrzymać wyłącznie proces Areny (nigdy usługę SentinelX).
- Przywrócić archiwum kodu oraz spójny backup SQLite sprzed migracji.
- Zachować kopię nieudanego wdrożenia do analizy; nie kasować backupów.
- Ponownie sprawdzić integralność DB, API, listę turniejów, status #5 i health-check.
- Jeżeli nie ma pewności, czy baza lub kod zostały przywrócone w całości, pozostawić Arenę zatrzymaną i nie uruchamiać turnieju.

## Zakres wykonanych prac

Wykonano kontrolowany backup, staging migracji, synchronizację kodu, instalację jednostki systemd, handoff dokładnie zweryfikowanego PID-u i uruchomienie dedykowanej usługi. Nie restartowano SentinelX. Nie nadpisano lokalnego `index.html` ani jego kopii. Nie zmieniono modeli, nie uruchomiono inferencji, nie wznowiono i nie zmodyfikowano turniejów #4/#5. Jednostka pozostaje `disabled` do osobnej decyzji o starcie przy bootowaniu.
