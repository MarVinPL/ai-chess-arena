# Local AI Chess League — zasady ligi

## Cel
Stała liga lokalnych modeli GGUF na VPS. Zawodnicy i wyniki pozostają w historii; nowe modele można dopisywać w dowolnym momencie.

## Zasady rozgrywek
- Turniej podstawowy: każdy z każdym, mecz dwupartiowy (każdy model raz białymi i raz czarnymi).
- Wyniki partii i turniejów są trwałe; dodanie nowego modelu nie resetuje rankingu ani historii.
- Nowy zawodnik rozgrywa mecze kwalifikacyjne z aktywnymi zawodnikami; później może wejść do pełnej ligi.
- Remisy, zwycięstwa, porażki, mat, pat, powtórzenia, reguła 50 posunięć i pozostałe zakończenia zapisujemy osobno.
- Ruchy waliduje python-chess. Nielegalny ruch nie jest wykonywany i trafia do statystyk.

## Czas
- Brak limitu czasu na ruch i brak automatycznego odrzucania wolnych modeli.
- Mierzymy czas każdego ruchu, średnią i medianę czasu ruchu oraz łączny czas partii.
- Zawieszoną partię można ręcznie zatrzymać; przerwanie jest oznaczone w historii, nie jako przegrana za czas.
- Szybkość ma osobną tabelę; nie zmienia wyniku Elo.

## Rankingi
- Główny ranking Elo jest liczony wyłącznie z wyników partii.
- Osobna klasyfikacja szybkości: czas ruchu i czas całej partii.
- Dodatkowe statystyki: W/D/L, skuteczność, nielegalne ruchy, zakończenia, RAM szczytowy/średni i konfiguracja inferencji.
- Dwa rozdzielone formaty: `PURE_AI` (bez podpowiedzi silnika) i `AI_ENGINE` (osobne zasady wsparcia silnikiem). Wyników tych formatów nie mieszamy.

## Powtarzalność i uczciwość
- Dla każdej wersji zawodnika zapisujemy nazwę pliku, SHA-256, metadane GGUF, rozmiar, runtime i parametry inferencji.
- W obrębie turnieju utrzymujemy wspólny prompt, format odpowiedzi, temperaturę i zasady walidacji; różnice wymagane przez chat template zapisujemy jawnie.
- Rejestrujemy wersję aplikacji i zestaw zasad turnieju.
- Nowy plik o tej samej nazwie, ale innym SHA-256, jest nową wersją zawodnika.

## Zasoby i bezpieczeństwo
- Modele uruchamiamy sekwencyjnie, jeden naraz; nie ładujemy kilku modeli równocześnie na tym VPS.
- GGUF są pobierane do prywatnego `managed-models/` wyłącznie na potrzeby aktualnego testu lub partii. Po zakończeniu pracy własny proces llamafile jest zatrzymywany, a niepotrzebne kopie Areny są usuwane; manifest (nazwa, źródło, SHA-256, rozmiar i licencja) pozostaje w bazie do ponownego pobrania.
- Przed partią sprawdzamy miejsce na brakujące pliki tej partii plus 512 MiB marginesu. Brak miejsca blokuje start tej partii, nie kasuje turnieju ani historii.
- Katalog współdzielony `/home/debian/ai-agent/models` jest całkowicie poza zakresem odczytu, zapisu i sprzątania Areny.
- API modelu i arena pozostają na loopback; nie wystawiamy niezabezpieczonych portów publicznie.
- Bez płatnych API, subskrypcji i nowych kosztów. Nowe modele tylko darmowe, po sprawdzeniu licencji i dostępnego miejsca.

## Status implementacji (2026-11-13)
- Działa rejestr modeli i wersji z SHA-256 dla lokalnych plików GGUF.
- Działa planowanie turnieju każdy z każdym: dwie partie na parę, z zamianą kolorów.
- Działa obliczanie osobnych rankingów Elo PURE_AI i AI_ENGINE (start 1200, K=32).
- Aktywne API: GET /api/league, /api/league/engine, /api/tournaments, /api/storage; POST /api/models/register i /api/tournaments.
- Zachowano historię gier oraz eksport PGN/CSV.
- Testy regresji: 8/8 zaliczone. SQLite quick_check: ok.
- Serwer został uruchomiony ponownie z nowym kodem; API odpowiada na loopback 127.0.0.1:8090.
- Obecnie rejestr i historia ligi są puste. W katalogu znajduje się jeden GGUF (~1.1 GB), ale pochodzenie i licencja konkretnego pliku nie zostały potwierdzone, więc nie został dodany do aktywnej ligi.
- Automatyczne pobieranie na żądanie, kontrola użycia pliku i bezpieczne usuwanie wyłącznie tymczasowych kopii areny pozostają do implementacji. Arena nie usuwa obecnie żadnych plików modeli.

## Magazyn modeli — etap 1 (2026-11-13)
- Dodano `model_store.py`: pobieranie z HTTPS z wymaganym SHA-256, znanym rozmiarem, jawnym zatwierdzeniem licencji i marginesem 512 MiB.
- Pobieranie jest atomowe (`.part` → walidacja → rename); błędne/niepełne pobrania są odrzucane.
- Arena-owned copies są izolowane w `managed-models/`; sprzątanie nie dotyka katalogu współdzielonego i sprawdza otwarte deskryptory.
- Testy całego projektu: 17/17.
- Ograniczenie: moduł nie jest jeszcze wywoływany przez API/UI, a silnik meczów nie jest zaimplementowany. Brak pobrań i brak usuwania modeli w tym etapie.

## Magazyn w panelu/API — etap 2 (2026-11-13)
- Podłączono `model_store.py` do `server.py`: `POST /api/models/download`, `POST /api/models/cleanup`.
- `GET /api/models` ujawnia identyfikator wersji i flagę `managed_copy`; `GET /api/storage` pokazuje osobny katalog i wolne miejsce.
- Panel ma formularz pobierania z URL HTTPS, rozmiarem, SHA-256, ID wersji i potwierdzeniem licencji; pokazuje kopie areny i pozwala usunąć wyłącznie takie kopie po potwierdzeniu.
- API wymaga jawnego pola potwierdzenia pobrania/usunięcia. Samo UI potwierdzenie nie zastępuje weryfikacji licencji w bazie.
- Testy całego projektu: 24/24; składnia JS i Python poprawna.
- Nie pobrano żadnego GGUF; nie usunięto żadnych plików. Runner meczów nadal nie jest gotowy.

## Runner turniejowy — etap 3 (2026-11-13)
- Dodano `engine_runner.py`: pojedynczy proces llamafile, sekwencyjne przełączanie GGUF pomiędzy ruchami, walidacja UCI przez `python-chess`, pomiar czasu i PGN z prawidłowymi nazwami zawodników.
- Dodano API start/stop turnieju: `POST /api/tournaments/start` i `POST /api/tournaments/stop`.
- Start sprawdza obecność plików i jawne zatwierdzenie licencji przypiętych wersji. Pliki współdzielone są tylko odczytywane; runner ich nie usuwa.
- Historia partii otrzymuje `tournament_id`, `pairing_id`, czasy ruchów i metadane inferencji. Niedokończone parowania pozostają do wznowienia.
- Testy: 30/30 zaliczone; nie uruchomiono prawdziwego GGUF i nie pobrano/usunięto modeli.

## Dwa lekkie modele — pierwszy test rzeczywistej inferencji (2026-11-13)

| Zawodnik | Repozytorium / rewizja | Kwantyzacja | Rozmiar | SHA-256 |
|---|---|---:|---:|---|
| Qwen2.5-0.5B-Instruct | `Qwen/Qwen2.5-0.5B-Instruct-GGUF` / `6dd44a1fb35d11b5d1b28902876ce3cc9e882d0e` | Q4_K_M | 491,400,032 B | `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db` |
| SmolLM2-360M-Instruct | `QuantFactory/SmolLM2-360M-Instruct-GGUF` / `25eef036a8d245a5a188b1b45d4b240a39bc9f24` | Q4_K_M | 270,590,592 B | `75c4346ef9e855ed630f80078a2430cf63aaca599e340360998a313070fcdc47` |

Obie wersje mają metadane licencji Apache-2.0, zostały przypięte do konkretnych rewizji HF i pobrane do `managed-models/` z kontrolą rozmiaru i SHA-256. Skan HF: status safe, VirusTotal 0 wykryć; informacyjny PAIT-GGUF-100 oznacza obecność chat template w metadanych GGUF, nie wykrycie złośliwego kodu.

Test prawdziwej inferencji na pozycji startowej, sekwencyjnie (bez równoczesnego ładowania): Qwen zwrócił legalne `g1h3` w 4.8479 s; SmolLM2 zwrócił legalne `g1h3` w 5.4184 s. To test techniczny jednego ruchu na model, nie ocena siły gry ani zakończona partia.


## Aktualny stan — magazyn per partia (2026-11-13)
- Arena używa wyłącznie `/home/debian/ai-chess-arena/managed-models/`; katalog współdzielony nie jest ścieżką awaryjną.
- Odpowiedzi Passkey są zapamiętywane per zawodnik między turniejami. Zadawane są tylko brakujące pytania z puli trzech; po poznaniu wszystkich odpowiedzi można wykonać najwyżej jedno losowe pytanie kontrolne na turniej. Po teście runtime jest zatrzymywany, a pliki cache usuwane.
- Przed partią przygotowywane są dwa modele tej partii. Po jej zakończeniu runtime Areny jest zatrzymywany, a obie kopie są usuwane, jeśli nie są używane. Kolejna partia pobiera je ponownie z tego samego źródła HTTPS i weryfikuje SHA-256.
- Czyszczenie zachowuje manifest wersji w SQLite, dzięki czemu planowane/oczekujące parowania można wznowić bez utrzymywania GGUF na dysku.
- Kontrola przestrzeni dyskowej jest wykonywana dla aktualnej partii: rozmiar brakujących modeli plus margines 512 MiB. Nie zmienia statusu turnieju ani zapisanych wyników.
- Sprzątanie obejmuje wyłącznie `managed_copy=1` w prywatnym katalogu Areny; zatrzymanie procesu i kontrola użycia pliku poprzedzają usunięcie.
- Testy regresji po zmianach magazynu per-partia: 38/38 zaliczone.
- Zmiany kodu są zapisane lokalnie i czekają na bezpieczne przeładowanie serwera; istniejący turniej #2 jest nadal aktywny, więc nie został przerwany.
