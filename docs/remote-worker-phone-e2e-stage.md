# Android Worker — izolowany test jednego ruchu (#276)

Uruchomienie testu nie wymaga nowego tokenu ani pobierania modelu.
Aplikacja Android 0.1.2 korzysta z tego samego ID i zaszyfrowanego
tokenu, lecz kieruje żądania HTTPS do dedykowanej domeny testowej.

Program remote_worker_phone_stage.py udostępnia tylko testowy koordynator
HTTP na adresie 127.0.0.1:8789; połączenie z zewnątrz odbywa się
wyłącznie przez Caddy (ops/arena-worker-test.caddy). Nie używa bazy
turniejów produkcyjnych podczas obsługi HTTP.

Tryb prepare tworzy prywatną bazę testową w katalogu 0700,
z uprawnieniami pliku 0600. Z bazy produkcyjnej otwartej mode=ro
kopiowany jest jedynie kryptograficzny skrót istniejącego tokenu
konkretnego Workera (bez ujawniania sekretu). Ważność testowego
weryfikatora jest krótsza, maksymalnie 1 godzina. Po teście całą
bazę tymczasową należy usunąć.

Bez jawnego lokalnego polecenia arm telefon nie otrzyma zadania.
Po uzbrojeniu kolejne poprawne ready od autoryzowanego telefonu
z oczekiwanym SHA przygotuje dokładnie jeden przydział v2
dla startowej pozycji szachowej. Wyliczona przez Arenę pełna
lista legalnych UCI jest przekazana do llama.cpp w telefonie.

Telefon wysyła wynik, Arena weryfikuje legalność i wykonuje
transakcję SQLite IMMEDIATE: zapis checkpointu z nowym FEN
oraz zwolnienie dzierżawy. Błąd atomowego zatwierdzenia
nie może ustawić Workera jako idle ani zmienić checkpointu
częściowo. Powtórne uzbrojenie jest zabronione.

Kontrola po stronie serwera:
python remote_worker_phone_stage.py status --db /ścieżka/etap.sqlite3
Powinno zwrócić phase=completed, dokładnie jeden ruch
i brak nierozliczonego lease. Test nie nadaje uprawnień
admina i nie przydziela produkcyjnych zadań.

W telefonie: ustawić testowy adres
https://arena-worker-test.148-113-182-137.sslip.io,
sprawdzić HTTPS, użyć Start lokalnie. Po teście wrócić
do https://arena.148-113-182-137.sslip.io.

Warunki wdrożenia: merge PR, CI PASS, kontrola przed
przeładowaniem Caddy. Nie modyfikować głównego Caddyfile,
nie restartować produkcyjnego turnieju, nie rotować tokenu.
