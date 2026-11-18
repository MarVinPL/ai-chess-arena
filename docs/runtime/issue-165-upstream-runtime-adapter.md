# Issue #165 — kontrakt implementacji adaptera upstream llama-server

Status: **specyfikacja implementacji; bez adaptera uruchamialnego, builda, instalacji i inferencji**.

## Cel i granice

Następny kodowy etap ma dodać alternatywny adapter procesu/API dla upstream `llama-server`. Adapter pozostaje odseparowany od obecnego `LlamafileRuntime`; nie wolno podmieniać istniejącego binarium ani portu. Domyślna ścieżka produkcyjna nadal oznacza llamafile na `127.0.0.1:8088`.

Ten dokument doprecyzowuje kontrakt Issue #165. Nie jest zgodą na budowanie ani uruchamianie runtime.

## Docelowa architektura

- Nowa klasa/moduł adaptera upstream, bez dziedziczenia, które mogłoby przypadkowo zmienić zachowanie llamafile.
- Profil upstream: loopback `127.0.0.1:8089`, jeden slot, CPU-only, offline, bez Web UI i warmup.
- Wybór runtime przez jawne, walidowane ustawienie. Wartość domyślna i brak ustawienia wybierają obecny llamafile. Nieznana wartość kończy się błędem konfiguracji.
- Nie ma automatycznego fallbacku pomiędzy runtime. Awaria upstream ma być widoczna jako awaria, a nie ciche uruchomienie innego binarium.
- Integracja z `SequentialChessRunner` musi zachować semantykę `generate()`, `stop()`, `shutdown()`, statusów, błędów, pomiaru czasu i braku limitu czasu odpowiedzi inferencji.
- Adapter zarządza wyłącznie procesem potomnym, który sam uruchomił. Watchdog może restartować tylko ten proces i tylko przy nadal aktywnym żądaniu modelu. Intencjonalny `stop()` kasuje zamiar restartu.
- Nie wolno zabijać procesu po nazwie, PID znalezionym globalnie ani portu zajętego przez inny proces.

## Walidacja i bezpieczeństwo

1. Binarium musi być jawnie wskazane, istnieć, być plikiem wykonywalnym i znajdować się w odrębnym katalogu runtime.
2. Ścieżka modelu musi wskazywać istniejący plik GGUF w dozwolonej lokalizacji przekazanej przez istniejący mechanizm model store; adapter nie wyszukuje ani nie pobiera modeli.
3. Host musi być loopback; port musi być 8089 i nie może kolidować z 8088.
4. argv musi wymuszać jeden slot, CPU-only, offline, brak Web UI i brak warmup.
5. Health i odpowiedzi chat completions muszą być walidowane fail-closed. Nieprawidłowy status, JSON lub kształt odpowiedzi nie może być interpretowany jako poprawna odpowiedź.
6. Timeout startu pozostaje odrębny od czasu inferencji. Nie wprowadzać nowego limitu czasu ruchu.
7. Błąd, timeout lub restart adaptera nie może zmienić aktywnego runtime, allowlisty, stanu zawodnika ani stanu turnieju.

## Testy wymagane w Issue #165

- Testy argv i walidacji profilu bez uruchamiania komendy.
- Mockowany `Popen`: start, proces już zakończony, terminate/wait, kill po timeout, brak wpływu na procesy obce.
- Mockowane HTTP: health ready/not-ready, chat completion, błędny status/JSON/kształt.
- Watchdog: brak restartu po intencjonalnym stop, restart wyłącznie własnego procesu, zachowanie po nieudanym restarcie.
- Test wyboru runtime: domyślnie llamafile, jawnie upstream, nieznana wartość fail-closed, brak automatycznego fallbacku.
- Testy integracyjne z `SequentialChessRunner` na atrapach runtime, bez silnika i bez modeli.
- Pełny zestaw testów projektu, kontrola składni i `git diff --check`.

Żaden test z tego zakresu nie może uruchamiać prawdziwego `llama-server`, llamafile, GGUF, inferencji, benchmarku ani turnieju.

## Kryteria ukończenia

- Domyślny runtime, konfiguracja systemd, baza, port 8088 i produkcyjny katalog wydania pozostają niezmienione.
- Kod upstream jest odseparowany i domyślnie wyłączony.
- CI przechodzi; PR zawiera komendy i wyniki testów.
- Issue pozostaje otwarte do czasu merge PR z implementacją i potwierdzenia wszystkich kryteriów.

## Poza zakresem — wymaga osobnej zgody

- Wybór i pinning konkretnego pełnego commitu/tagu upstream do builda.
- Build binarium (preferowany darmowy CI/runner poza VPS), SBOM, licencje, checksumy i archiwizacja artefaktów.
- Pobieranie lub instalacja binarium na VPS.
- Test rzeczywistego binarium, test z modelem, benchmark, allowlist admission, aktywacja i deploy.

Nie dotykać `/home/debian/ai-agent/models`. Nie uruchamiać turniejów #4/#5. Modele w kwarantannie pozostają w kwarantannie.

## Powiązane

- Issue #163 — plan i inertny kontrakt: https://github.com/MarVinPL/ai-chess-arena/issues/163
- Issue #165 — adapter upstream: https://github.com/MarVinPL/ai-chess-arena/issues/165
