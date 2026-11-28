# Inicjalizacja dzienników aktualizacji Android Worker (#417)

Android udostępnia przycisk **Uzgodnij dzienniki aktualizacji**. Aplikacja używa
zapisanego tokenu i odczytuje z PackageManager nazwę pakietu, historyczny podpis
oraz zainstalowany versionCode. Aktywna lub niejednoznaczna sesja, zachowany
callback, nieznana historia instalacji albo lokalny rekord wymagający
uzgodnienia blokują inicjalizację. Brak widocznej sesji i brak sekretu nie
stanowią potwierdzenia zakończonej aktualizacji.

Dla nowej i starszej instalacji bez dzienników źródłem zgody jest jawny wpis
koordynatora. Operator musi wcześniej niezależnie potwierdzić tożsamość
urządzenia, jego wersję oraz rozliczenie ewentualnej poprzedniej aktualizacji.
Jeśli nie ma takich dowodów, pozostawia Workera zablokowanego i zachowuje dane.
Przykład dla istniejącej **izolowanej bazy testowej**, po przygotowaniu
poświadczenia Workera:

~~~sh
python -m tools.approve_worker_journal_baseline \
  --db /ścieżka/do/staging.sqlite3 \
  --worker-id android_test_01 \
  --origin https://arena-test.example \
  --version-code 1002 \
  --confirm-reviewed-baseline
~~~

Wpis wiąże dokładny Worker ID, aktualny token, origin HTTPS i versionCode.
Jest przypisywany do pierwszego nonce i dopuszcza wyłącznie ponowienie tej
samej transakcji. Koordynator sprawdza stan zadań, rezerwacji i checkpointów
w transakcji. Dla nowego Workera bez wpisu stanu tworzy wpis unknown tylko pod
blokadą. Dla istniejącego Workera pierwsze pozyskanie blokady wymaga świeżego
stanu idle. Zatwierdzenie pozostawia unknown do niezależnego uzgodnienia
gotowości. Koordynator korzysta wtedy z istniejącej ścieżki potwierdzenia
bezczynności po ponownym połączeniu Workera i osobnym potwierdzeniu operatora.
Starszy serwer bez obsługi zgody nie odblokuje dzienników.

W razie zerwania łączności użyj tego samego adresu, Worker ID i tokenu.
Nie kasuj dzienników, poświadczeń, danych aplikacji ani wpisów koordynatora.
Procedura przerwań jest opisana w issue-416-journal-recovery.md.
Test na fizycznym urządzeniu i migracja produkcyjna pozostają w #311 i #407.
