# Blokada inicjalizacji dzienników Workera (#420)

Klient wysyła przez istniejący punkt wejścia HTTPS żądanie `POST /api/remote-workers/journal-init-acquire` z obiektem JSON
zawierającym dokładnie `worker_id`, aktualny token maszynowy `token` i losowy
`nonce` zapisany jako 32 małe znaki szesnastkowe. Żądanie korzysta ze standardowych
limitów HTTP Workera. Po zatwierdzeniu blokady koordynator odpowiada kodem 200
oraz zwraca wersję kontraktu (`contract_version: 1`), `authenticated: true`,
`source: "coordinator"`, powiązane `worker_id` i `nonce`,
`checked_at_unix_ms`, `fenced: true` i `install_allowed: false`.

SQLite serializuje pozyskanie blokady i konkurencyjne zapisy przez
`BEGIN IMMEDIATE`. Koordynator wymaga stanu `idle_confirmed` sprzed najwyżej
30 sekund, bez identyfikatora ani terminu zadania, przydziału, rezerwacji
(również po terminie), żądania zatrzymania, punktu wznowienia, oczekującego
odzyskania po aktualizacji i wyniku oczekującego na zastosowanie. W tej samej
transakcji zapisuje identyfikator Workera, nonce, skrót SHA-256 tokenu i czas
utworzenia. Zapis używa `synchronous=FULL`, a ważność tokenu jest sprawdzana
ponownie przed zatwierdzeniem. Triggery SQLite chronią ścieżki zapisujące przydział, gotowość
i rezerwacje przez oddzielne połączenia z bazą. Restart nadal może unieważnić
stan zadania do `unknown`.

Nieprawidłowy dokument daje 400, nieaktualne poświadczenia 401, konflikt 409,
przekroczenie limitu żądań 429, a błąd magazynu lub brak wymaganego schematu 503. Powtórzenie nonce, upływ
czasu, rotacja tokenu i restart pozostawiają istniejącą blokadę. Po utracie
odpowiedzi klient traktuje wynik jako nieznany. Odczytowe endpointy diagnostyczne
zwracają `unknown` w czasie blokady. Natywny klient należy do #421, atomowa
inicjalizacja obu dzienników do #422, a zatwierdzenie i zwolnienie blokady
do #423. Wdrożenie wymaga ukończenia i weryfikacji mechanizmu.
