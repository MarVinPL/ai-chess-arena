# Android Worker — aktualizator APK (Issue #196)

Stan: specyfikacja bezpieczeństwa; nie jest to gotowy instalator.

1. Wykrywaj tylko stabilne wydania worker-vX.Y.Z, nowsze od aktualnej wersji.
2. Pobieraj wyłącznie APK ARM64 przez HTTPS z zaufanego źródła, do prywatnego katalogu aplikacji, z limitem rozmiaru i postępem.
3. Weryfikuj SHA-256, podpis APK względem zainstalowanego certyfikatu, package name i versionCode. Sam SHA-256 obok APK nie uwierzytelnia wydawcy.
4. Blokuj instalację bez uwierzytelnionego i świeżego potwierdzenia idle od koordynatora. Sprawdzaj ponownie przed instalacją.
5. Uruchamiaj systemowy instalator Android przez FileProvider; wymagaj potwierdzenia użytkownika. Bez root i cichej instalacji.
6. Obsłuż błędny podpis, downgrade, brak miejsca, zerwane pobieranie, przekierowania i aktywne zadania.
7. Testy Android 16: scenariusze pozytywne i negatywne, szczególnie unknown idle.

Obecny backend nie ma jeszcze trwałego rejestru zadań; nie wolno uznawać unknown za idle.

## Implementacja etapu 1: filtr metadanych APK (bez pobierania)

Moduł `src-tauri/src/android_update_asset.rs` jest teraz kompilowany
razem z natywnym Workerem i ma własne testy Rust. Odrzuca:

- nieoficjalny URL, HTTP, przekierowanie do obcej domeny, query/fragment,
  nazwę z niedozwolonymi znakami i pliki bez oznaczenia ARM64/Workera;
- brak lub wieloznaczność odpowiedniego pliku APK, asset w stanie innym niż
  `uploaded`, rozmiar poza zakresem 1 KiB–200 MiB;
- brak albo niepoprawny digest `sha256:<64 małe znaki hex>`,
  błędny tag lub wersję wydania w wersji niezgodnej z żądanym tagiem.

Otrzymany kandydat jest **niezaufany**: hash z metadanych release pomaga
wykryć uszkodzenie transferu, lecz nie dowodzi tożsamości wydawcy. Moduł
**nie pobiera APK, nie uruchamia instalatora, nie pyta o uprawnienia i nie
omija `update_installation_readiness`**, które nadal zwraca
`can_install=false`. Do produkcyjnej aktualizacji nadal wymagane są
weryfikacja pobranych bajtów SHA-256, zgodności podpisu APK z zainstalowaną
aplikacją, package name, wyższego Android versionCode, świeżego
koordynatorowego idle, atomowego zapisu do prywatnego katalogu, systemowego
FileProvider/PackageInstaller z potwierdzeniem użytkownika oraz testy na
fizycznym Androidzie.

Branch PR #197 jest starszą gałęzią zależną od #195; przy integracji z
bieżącym Workerem należy rozstrzygnąć różnice w GUI i release channels.
Nie deklarować kompletnej instalacji ani PASS bez uruchomienia Rust/Android CI.

## 2026-11-22 — zgodność ze współczesnym workflow Android

Workflow `android-worker-apk.yml` został ujednolicony z przetestowanym
wariantem z `main`: `actions/checkout@v7`, `setup-node@v7`,
`setup-java@v6`, `upload-artifact@v7`, oficjalny Android CLI 22.0
ze sprawdzanym SHA-256, Android 16 API 36 i NDK 27.2.12479018.
Nie używa `sdkmanager`. Wersja `setup-python@v7` jest również
zapisana w `python-tests.yml`; w repozytorium została dopuszczona do
allowlisty.

Walidator wybiera **tylko stabilne wydania** `worker-vX.Y.Z`
(`draft=false`, `prerelease=false`). Testy regresyjne sprawdzają
również odrzucenie brakujących flag oraz tagów `-rc` i innych
niekanonicznych wersji. Istniejący kanał GUI Pre-release służy
wyłącznie do odczytu informacji o wydaniu; jego obsługa przez
instalator jest osobnym zagadnieniem i **nie jest włączona**.

Powyższe zmiany nie przesądzają wyniku CI gałęzi #197, która jest
stacked PR z bazą #195, ani nie upoważniają do instalacji APK.

## 2026-11-22 — streaming SHA-256 dla przygotowanego pliku APK

`android_update_asset::verify_apk_stream` otrzymuje **już otwarty**
strumień pliku z prywatnego stagingu. Sprawdza rozmiar z metadanych
wydania (zakres 1 KiB–200 MiB), czyta w porcjach maksymalnie 64 KiB,
odrzuca bajty przekraczające deklarowany rozmiar, ucięte transfery,
błędy odczytu i niezgodność SHA-256. Implementacja używa `sha2=0.10.9`
już obecnego w `Cargo.lock` i osobnych testów Rust. Testy statyczne
Python pilnują istnienia ograniczeń i spójności wersji zależności.

**Ta funkcja sama nie pobiera danych, nie zapisuje plików i nie jest
komendą Tauri.** Nie jest uwierzytelnieniem wydawcy: digest z GitHub
Release nie zastępuje podpisu APK ani kontroli `packageName`,
`versionCode` i świeżego idle z koordynatora. Brak podłączonego
stagingu, verifiera certyfikatu i instalatora: instalacja nadal **NO-GO**.

## Audyt akceptacji Issue #196 (2026-11-22, kod PR #197)

Poniższe statusy wynikają z przeglądu konkretnych wywołań i punktów
integracji — sama obecność nazwy funkcji lub testu statycznego
**nie jest dowodem działania funkcjonalności**.

| Warunek Issue #196 | Ocena | Dowód i brakująca integracja |
|---|---|---|
| Wykrywanie stabilnego `worker-vX.Y.Z` | IMPLEMENTED (bez CI na HEAD) | `lib.rs::check_worker_release` czyta metadane GitHub i filtruje wersje; brak release feedu dla autoryzowanego instalatora |
| Oficjalny URL, ARM64, limit rozmiaru, digest metadanych | IMPLEMENTED (bez CI na HEAD) | `android_update_asset.rs::select_apk_candidate`; **nigdzie nie wywołano tej funkcji w ścieżce aktualizacji UI** |
| SHA-256 rzeczywistych bajtów pobranego APK | PARTIAL | `verify_apk_stream` liczy digest czytanego strumienia, testy Rust używają danych w pamięci; **brak pobierania pliku i wywołania tej funkcji na pliku APK** |
| Pobieranie APK przez HTTPS do prywatnego katalogu + postęp | NOT IMPLEMENTED | Brak funkcji transferu, tymczasowego pliku, raportowania postępu do Tauri/GUI |
| Podpis APK równy podpisowi zainstalowanej aplikacji | NOT IMPLEMENTED | Istnieje weryfikacja artefaktu w GitHub CI, ale **nie ma kontroli certyfikatu w aplikacji na Androidzie** |
| `packageName` i wyższy `versionCode` | NOT IMPLEMENTED | `tauri.conf.json` zawiera identyfikator aplikacji; brak odczytu i porównania metadanych kandydata APK |
| Koordynatorowe, uwierzytelnione i świeże `idle` przed instalacją | NOT IMPLEMENTED | `update_installation_readiness` celowo zawsze zwraca `can_install=false`; brak połączenia weryfikacji z instalatorem |
| Systemowy Android FileProvider/PackageInstaller + potwierdzenie użytkownika | NOT IMPLEMENTED | Brak komendy Tauri, mostu Android i intencji instalatora |
| Przycisk „Pobierz aktualizację” i pasek postępu | NOT IMPLEMENTED | Stary `public/index.html` zawiera jedynie „Sprawdź aktualizacje”; przycisk „Aktualizuj” i kanały Latest/Pre-release są w **nowszej gałęzi PR #193**, nie w bazie tego PR |
| Testy pozytywne/negatywne Android 16 | PARTIAL | Istnieją jednostkowe testy Rust metadanych i strumienia oraz statyczne Python; **brak uruchomionego Android e2e** |
| Brak root/płatnych usług i odmowa instalacji przy błędach | PARTIAL / SAFE BLOCKED | Instalacja w całości zablokowana; kompletnej obsługi błędów, pobierania i instalatora jeszcze nie ma |

**Wniosek: NO-GO dla scalenia Issue #196 jako zakończonego.** W szczególności
żaden test kodu, który nie jest wywoływany przez GUI/Android, nie
upoważnia do oznaczenia wymagania jako `DONE`. Na badanym HEAD nie
ma potwierdzonego przebiegu Python/Rust/Android CI. Weryfikacja APK przez
GitHub Actions to kontrola buildu, **nie kontrola kandydata na telefonie**.

### Warunki faktycznego zamknięcia Issue

- Prześledzić pełną wywoływaną ścieżkę:
  `GUI → Tauri → GitHub Release → prywatny download z postępem →
  SHA-256 pliku → certyfikat/package/versionCode → świeże idle →
  systemowy instalator z potwierdzeniem`.
- Udowodnić testami integracyjnymi przypadki: poprawna aktualizacja,
  zły podpis/hash/package/versionCode, obcy URL/przekierowanie, brak
  miejsca, przerwane pobieranie, nieznany/aktywny job, restart procesu.
- Potwierdzić testy na Android 16 oraz brak regresji przy wyborze kanału
  Latest/Pre-release na aktualnej gałęzi Workera.
- Sprawdzić wynik GitHub CI **konkretnego HEAD** po integracji z aktualnym
  Workerem. PR #197 pozostaje draft do spełnienia tych kryteriów.

## 2026-11-22 — dowód faktycznego podłączenia filtra release → UI

`check_worker_release` (komenda Tauri wywoływana przez GUI) teraz
**rzeczywiście wywołuje**
`android_update_asset::select_apk_candidate(data, tag)`
dla najwyższego stabilnego wydania. Pole odpowiedzi
`apk_asset_available` jest `true` tylko dla nowszej wersji z
jednoznacznym plikiem APK ARM64 spełniającym ograniczenia metadanych.
Gdy brak wydania/kandydata, wartość jest `false`.

Widok `public/index.html` używa tego pola, aby wyświetlić, czy
kwalifikujący się APK jest w wydaniu. Wynik to wyłącznie **status
metadanych**, a nie dowód sprawdzonego certyfikatu APK ani gotowej
instalacji. Pobieranie i instalacja nadal są zablokowane.

Dodatkowo odpowiedź GitHub Releases ma twardy limit 1 MiB przed
dekodowaniem JSON, obejmujący również serwer niepodający Content-Length.
Regresyjny test `test_native_release_checker_actually_uses_candidate_gate_and_displays_result`
sprawdza połączenie: komenda Tauri → selektor APK → odpowiedź → UI.
To kontrola integracji kodu, nie test systemowy Android 16.

**Audyt Issue #196 aktualizujemy tylko w zakresie**: `select_apk_candidate`
nie jest już niewywoływany; jednak `verify_apk_stream` **wciąż**
nie jest podłączony do rzeczywiście pobranego pliku. Pobieranie,
postęp, podpis APK, `packageName`, `versionCode`, trwałe idle i
systemowy instalator nadal **NOT IMPLEMENTED/NO-GO**.

## 2026-11-22 — pobieranie prywatnego APK (prototyp podłączony)

Dla Androida GUI teraz oferuje **Pobierz APK** tylko gdy natywna
weryfikacja metadanych wykryła nowszy stabilny APK ARM64. Komenda
`download_worker_apk` nie przyjmuje od JS adresu, hasha, nazwy pliku
ani katalogu: ponownie odczytuje oficjalne wydania z GitHub i wybiera
własny kandydat `select_apk_candidate`, by zapobiec zmianie wersji lub
URL między sprawdzeniem a pobieraniem.

Moduł `android_update_download.rs` ogranicza dozwolone przekierowania
do HTTPS `release-assets.githubusercontent.com` i
`objects.githubusercontent.com` (maksymalnie 3 przekierowania),
bez danych uwierzytelniających w URL. Odpowiedź jest pobierana porcjami
z limitem 200 MiB; deklarowana liczba bajtów i SHA-256 są sprawdzane
przed zakończeniem. Plik tymczasowy powstaje przez `create_new`
w prywatnym `app_cache_dir/worker-apk-updates`; błąd usuwa częściowy
plik. Po pozytywnej weryfikacji integralności wykonywana jest zmiana
nazwy pliku. Postęp jest przekazywany jako zdarzenie Tauri, bez
ujawniania ścieżki pliku lub tokenu w JS.

**Status rzeczywisty:** kod jest podłączony od GUI do komendy Tauri,
jednak nie potwierdzono działania na Android 16 ani wyników
Rust/Android CI na aktualnym HEAD. Walidacja samego SHA-256
**nie weryfikuje tożsamości podpisującego**. Plik **nie jest instalowany,
przekazywany systemowemu instalatorowi ani uznawany za zaufany**.
Nadal brakuje zweryfikowania certyfikatu APK względem zainstalowanej
aplikacji, `packageName`, wyższego `versionCode`, świeżego
koordynatorowego `idle`, obsługi błędów na fizycznym Android 16 oraz
systemowego instalatora z potwierdzeniem. **Issue #196 pozostaje NO-GO**.

Starsze wpisy w tej dokumentacji odzwierciedlają stan historyczny na
podanym commicie; w aktualnym kodzie dostępne jest pobieranie do
prywatnej pamięci, ale **nie aktualizacja z instalacją**.

### Uzupełnienie: retry, równoległe pobieranie i kontrola CI

Dla etapu pobierania obowiązuje pojedynczy transfer w obrębie procesu
(`DownloadSlot` z atomowym zamkiem, zwalnianym przez `Drop`).
Przy ponownej próbie zachowany plik z cache jest przed użyciem
ponownie weryfikowany strumieniowo pod względem rozmiaru i SHA-256;
zły regularny plik cache jest usuwany, a niedokończony `.partial`
z poprzedniego procesu można usunąć przed pobraniem. Symlink pod
docelową nazwą APK nie jest akceptowany. Dodano test Rust blokady
równoległych pobrań i statyczny test ścieżki GUI → Tauri → staging.

Na czas przeglądu PR #197 oba workflowy dopuszczają także PR
celujący w `feat/issue-194-worker-defaults`, ponieważ jest to obecna
baza tego stacked PR. **Przed integracją do main usunąć tę tymczasową
gałąź z filtrów workflowów.** Nie traktować samego wpisu filtra
jako zakończonego CI: obecny odczyt przebiegów PR na HEAD nie
potwierdza Python/Rust/Android PASS. Dopiero rzeczywisty wynik
kompilacji i testy Android 16 mogą zatwierdzić tę funkcję.

## 2026-11-22 — istniejący certyfikat Workera w bramce tożsamości

Bez generowania nowego klucza: `android_update_identity.rs` stosuje
`WORKER_SIGNING_CERT_SHA256 =
3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12`,
czyli dokładnie ten sam odcisk co zaufany build `main` sprawdzający
klucz `WORKER_ANDROID_DEBUG_KEYSTORE_B64` w CI. Bramka wymaga
jednocześnie: identycznego `packageName` z konfiguracją Tauri,
`versionCode` większego od zainstalowanego, pojedynczego certyfikatu
obecnego podpisującego po obu stronach, poprawnego odcisku i
odcisku obu aplikacji równego przypiętemu certyfikatowi. Dwa pliki
podpisane **jednakowym, ale obcym** certyfikatem też są odrzucane.

Nie ma jeszcze mostu Android PackageManager odczytującego te informacje
z realnie zainstalowanego pakietu i pobranego APK. Z tego względu
ta polityka jest sprawdzalna testami jednostkowymi, ale nie może
autoryzować instalacji. **Klucz prywatny zostaje wyłącznie w GitHub
Secrets; do aplikacji wpisujemy jedynie publiczny odcisk SHA-256.**
Weryfikacji samego digestu GitHub Release nie wolno mylić z
weryfikacją tożsamości podpisującego APK.

## 2026-11-22 — odczyt Android PackageManager przez JNI (PR #197)

Rozwiązanie nie edytuje generowanych źródeł Tauri `gen/android`.
W `android_update_package_manager.rs` używa Android Context/JNI
(wersje `jni=0.21.1` i `ndk-context=0.1.1`, już zapisane w lockfile)
do odczytu `PackageInfo` z **zainstalowanego Workera** i
`getPackageArchiveInfo` z pliku APK w prywatnym cache. Odczytuje
`packageName`, `getLongVersionCode` i `SigningInfo`
(`getApkContentsSigners`), liczy SHA-256 bajtów certyfikatu
przez `Signature.toByteArray()`. Brak certyfikatu, kilku sygnatariuszy
lub błąd JNI → odmowa.

Komenda `verify_downloaded_worker_apk`, wywoływana po pobraniu,
**ponownie** wybiera oficjalny asset, sprawdza plik cache, jego
rozmiar i SHA-256, następnie porównuje obie tożsamości w
`android_update_identity::check_platform_apk_identity` z przypiętym
odciskiem certyfikatu **pochodzącego z GitHub Secret**, a nie z APK.
Widok pokazuje wynik, ale **nie ujawnia ścieżki pliku** i nie
wywołuje instalatora.

Pozostaje: sprawdzenie na prawdziwym Android 16, potwierdzony CI
dla aktualnego HEAD, niezależny świeży i uwierzytelniony stan
idle koordynatora oraz systemowy instalator z zgodą użytkownika.
Nawet pozytywne `PackageManager` oraz SHA-256 nie pozwalają
pominąć późniejszej kontroli Android PackageInstaller. **NO-GO
dla instalacji i zamknięcia Issue #196**.

## 2026-11-22 — kontrakt świeżego idle koordynatora (fail closed)

`check_update_safety` przesyła losowy 128-bitowy `nonce` (RNG systemu)
do istniejącego autoryzowanego endpointu HTTPS
`/api/remote-workers/update-safety`, wraz z `worker_id` i tokenem.
Odpowiedź `idle_confirmed` jest akceptowana jako **status odczytu**
dopiero, gdy JSON równocześnie zawiera:

- `state="idle_confirmed"`, `authenticated=true`,
  `source="coordinator"`, `active_tasks=0`;
- dokładny `worker_id` z zapytania i ten sam `nonce`;
- `checked_at_unix_ms` z wiekiem najwyżej 30 sekund, z tolerancją
  przesunięcia zegara w przyszłość najwyżej 5 sekund.

Brak pól, niezgodny nonce, uruchomione zadania, przeterminowana
odpowiedź, błąd RNG/HTTPS → `unknown`/błąd, nigdy `idle_confirmed`.
Istniejący backend może **jeszcze nie implementować tego kontraktu**:
to świadomie oznacza `unknown` i wymaga osobnego, udokumentowanego
podłączenia trwałego rejestru zadań po stronie koordynatora. Nie
wolno interpretować braku danych jako zgody na instalację.

**Ważne:** nawet poprawny status odczytu NIE otwiera instalatora:
`update_installation_readiness` nadal wymusza `can_install=false`.
Do instalacji potrzebny będzie ponowny nonce-bound odczyt bezpośrednio
przed Android PackageInstaller, a także kontrola podpisu APK i
świadoma zgoda użytkownika.

Komponent używa bezpłatnego `getrandom=0.2.17` już istniejącego w
Cargo.lock, bez nowych usług i bez udostępniania sekretu podpisującego.

## 2026-11-22 — serwerowa rezerwacja aktualizacji i zwolnienie

Koordynator obsługuje teraz dwa **oddzielne** endpointy HTTPS z
uwierzytelnieniem istniejącym tokenem Workera i losowym `nonce`:

- `POST /api/remote-workers/update-reserve` przyjmuje `worker_id`,
  `token`, `nonce`. Rezerwuje przez `BEGIN IMMEDIATE` jedynie
  świeży `idle_confirmed` i zwraca jednorazowy 256-bitowy
  `hold_token` oraz `expires_at_unix_ms`. Odpowiedź zawsze zawiera
  `install_allowed=false`.
- `POST /api/remote-workers/update-release` przyjmuje dodatkowo
  `hold_token`. Usuwa rezerwację tylko przy dokładnym dopasowaniu
  sekretu i przestawia stan Workera na `unknown`: wymagane będzie
  ponowne zaufane uzgodnienie stanu przed przyszłym zadaniem.

Rezerwacje nie są automatycznie zwalniane przy upływie TTL ani po
restarcie. Zaufany koordynator może je zwolnić oryginalnym tokenem
również po wygaśnięciu TTL, ale nie może na tej podstawie uznać
Workera za gotowego do zadań. Brak tokenu lub błędny token ⇒ brak zmian.

**NIE PODŁĄCZONO jeszcze wywołania rezerwacji do interfejsu Workera.**
Uruchamianie rezerwacji bez gotowego procesu instalacji mogłoby
pozostawić trwale zablokowane przydziały. Następny krok to
orkiestracja po stronie aplikacji bezpośrednio przed wywołaniem
systemowego instalatora, obsługa anulowania i wznowienia po
restarcie. Endpoint nie jest dowodem gotowego instalatora.

## 2026-11-22 — weryfikacja możliwości zgłoszenia instalacji w Androidzie

Po pomyślnym sprawdzeniu SHA-256 oraz zgodności identyfikatora pakietu,
`versionCode` i certyfikatu `verify_downloaded_worker_apk` odczytuje
przez JNI `PackageManager.canRequestPackageInstalls()`. W razie
braku uprawnienia, braku API albo błędu JNI wynik to `false`.
Interfejs wyraźnie odróżnia brak uprawnienia Androida od błędnego
podpisu APK. Jest to **tylko diagnostyka**: `true` nie oznacza
instalacji ani zgody użytkownika.

Do aktywacji systemowego instalatora potrzebne będą jeszcze:
odpowiednio zdefiniowane i przetestowane na Android 16 uprawnienie
`REQUEST_INSTALL_PACKAGES`, obsługa ustawień Androida, kompletna
sesja `PackageInstaller` z `USER_ACTION_REQUIRED`, jej wynik i
anulowanie, powiązanie rezerwacji koordynatora z całym procesem
oraz odzyskiwanie po restarcie. Nie wolno traktować samego
`canRequestPackageInstalls()` jako zgody na bezobsługową instalację.

## 2026-11-22 — odzyskiwanie rezerwacji po utracie odpowiedzi

Dotychczas serwer generował `hold_token` dopiero przy rezerwacji.
Jeśli odpowiedź HTTPS ginęła po zatwierdzeniu transakcji SQLite,
aplikacja nie znała tokenu potrzebnego do zwolnienia blokady.

Protokół jest teraz **write-before-reserve**: natywna aplikacja Rust
losuje 256 bitów z systemowego RNG, zapisuje token razem z
`worker_id` i adresem koordynatora do prywatnego systemowego magazynu
poświadczeń (keyring; osobne konto `coordinator-update-hold-v1`),
weryfikuje odczyt i **dopiero potem** wysyła uwierzytelnione żądanie
HTTPS `update-reserve` z tym samym tokenem. Serwer sprawdza format
64 małych znaków szesnastkowych i zatwierdza go w trwałej transakcji
wymagającej świeżego idle. Odpowiedź musi potwierdzić **dokładnie**
zapisany wcześniej token. Wszelkie niepowodzenia pozostawiają rekord
do wyjaśnienia; nowej rezerwacji nie można nadpisać.

`update-release` pobiera token **z keyring**, nie z JavaScript,
weryfikuje `worker_id` i adres serwera, a po uwierzytelnionym
potwierdzeniu zwolnienia usuwa lokalny sekret. Błąd uwierzytelnienia,
sieci, magazynu lub niezgodne potwierdzenie nigdy nie kasują sekretu
i nie zwalniają blokady w sposób domniemany.

Po restarcie sam wpis w keyring **nie jest** powodem do automatycznego
zwolnienia: instalator Android może nadal działać. Przed
zwolnieniem należy uzgodnić faktyczny stan PackageInstaller i Workera.
Wywołania są nadal **wyłącznie wewnętrzne**, bez komend frontendowych:
brak kompletnej obsługi stanu systemowego instalatora i odzyskiwania,
więc NIE jest jeszcze dozwolone uruchamianie aktualizacji z GUI.

Nie wymaga żadnej płatnej usługi i **nie modyfikuje certyfikatu** z
`WORKER_ANDROID_DEBUG_KEYSTORE_B64`.

## 2026-11-22 — jawny status odzyskiwania po ponownym uruchomieniu

Dodano wyłącznie odczytową komendę Tauri `update_recovery_status`.
Sprawdza osobny wpis keyring rezerwacji, ale udostępnia interfejsowi
**tylko** `pending`, `blocked` i ogólny polski komunikat — bez
`hold_token`, adresu serwera i `worker_id`. Po starcie widok
„Aktualizacje aplikacji” pokazuje ten stan. Jeżeli rezerwacja oczekuje
lub odczyt magazynu się nie udał, widok blokuje „Zapisz token” i
„Usuń zapisany token”; natywny Rust niezależnie wymusza tę samą
blokadę w `store_worker_token` i `forget_worker_token`, żeby
ominięcie GUI nie spowodowało utraty uwierzytelnienia potrzebnego
do uwolnienia koordynatora.

To **nie jest automatyczne zwolnienie rezerwacji ani jej uzgodnienie
z Android PackageInstaller**. W stanie niepewnym rezerwacja pozostaje
zablokowana. Przed umożliwieniem instalacji niezbędny jest test
systemowego magazynu na fizycznym Android 16, trwała obsługa wyniku
systemowej instalacji i niezależne uzgodnienie koordynatora.

## 2026-11-22 — jawny model cyklu życia Android PackageInstaller

Dodano `android_update_install_state.rs` z czystą polityką przejść
`VerifiedStaged → CoordinatorReserved → SessionCreated →
SessionCommitted → AwaitingUserConsent`, oraz wynikami
`InstalledConfirmed`, `CancelledConfirmed`, `FailedConfirmed`
i `ResultUnknown` przy przerwaniu pracy.

Samo wywołanie `Session.commit()` ani zgłoszenie
`STATUS_PENDING_USER_ACTION` nie jest wynikiem instalacji.
Pakiet wolno przekazać instalatorowi dopiero po sprawdzeniu
wszystkich elementów: ważnej blokady koordynatora, SHA-256,
certyfikatu z istniejącego GitHub Secret, wyższego `versionCode`
i uprawnienia Androida do zlecenia instalacji. Wymagana jest
świadoma zgoda użytkownika systemu Android, nigdy silent install.
Zwolnienie rezerwacji dopiero po potwierdzonym przez OS wyniku
i niezależnym uzgodnieniu stanu aplikacji; restart lub brak
callbacku daje `ResultUnknown`, bez zgody na nową instalację
ani zwolnienie rezerwacji. Referencje:
https://developer.android.com/reference/android/content/pm/PackageInstaller.Session
oraz
https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams

**To polityka i testy — nie uruchomiony instalator.**
Nie dodano wywołania `Session.commit`, sesji PackageInstaller,
natywnego odbiornika statusów ani przycisku „Instaluj”. Należy
zintegrować odbiornik OS, trwały identyfikator sesji, odzyskiwanie
wyniku po restarcie, a dopiero potem podłączyć do koordynatora.
`update_installation_readiness` nadal `can_install=false`.

## Decyzja użytkownika 2026-11-22 — aktualizacja może przerwać zadanie

Nie należy uzależniać aktualizacji Android Workera od naturalnego końca
długiego obliczenia. Zamiast tego koordynator może wstrzymać aktywne
zadanie, gdy:

1. Wstrzyma nowe przydziały oraz poleci Workerowi przerwać inference.
2. Uzyska i trwale zatwierdzi **ostatni spójny checkpoint** zadania, wraz
   z identyfikatorem zadania oraz pozycją postępu; odpowiedź modelu w locie
   nie może zostać uznana za zatwierdzony wynik.
3. Potwierdzi, że Worker rzeczywiście przestał wykonywać obliczenia,
   i atomowo zapisze pauzę / możliwość wznowienia w rejestrze zadań.
4. Dopiero w tym momencie dopuści rezerwację aktualizacji. Instalację
   prowadzi Android za zgodą użytkownika, ze sprawdzonym podpisem APK.
5. Po restarcie uzgodni wersję aplikacji i wynik instalatora, zdejmie
   blokadę i ponownie przydzieli zadanie. Wznowienie jest od ostatniego
   zweryfikowanego checkpointu; gdy tego typu zadanie nie wspiera
   wznowienia, wykonuje się je **od początku**, bez podwójnego zapisu
   wyniku i bez kasowania wcześniej ukończonych zadań.

Arena ma już trwałe checkpointy turnieju i partii
(`docs/tournament-checkpoints.md`, Issue #98), łącznie z odrzucaniem
odpowiedzi inference trwającej podczas stop i ponownym rozpoczęciem
od ostatniego legalnego ruchu. **Zdalny Worker w tej gałęzi jest jednak
nadal prototypem bez połączonego wykonawcy zadań**, więc nie należy
traktować samego wpisu `idle_confirmed` w SQLite jako dowodu trwałej
pauzy. Wymaga to podłączenia prawdziwego stop/checkpoint/ack i
wznowienia do koordynatora. Do tego czasu fail-closed pozostaje
obowiązujące, a instalator nadal wyłączony.

W szczególności nie wolno "upraszczać" aktualizacji przez zakończenie
running job w bazie bez potwierdzenia zatrzymania Workera i zapisu
checkpointu. Sam timeout lub odłączenie urządzenia nie wystarczają.

## 2026-11-22 — trwałe żądanie przerwania zadania

`remote_worker_update_pause.py` wprowadza trwały koordynatorowy
rejestr `worker_update_pause_requests` z `request_id`, `job_id`,
`worker_id` i fazami `requested` / `acknowledged`.
Żądanie może być zapisane wyłącznie dla faktycznie aktywnego,
niewygasłego zadania, atomowo w SQLite (`BEGIN IMMEDIATE`), i
przetrwa restart koordynatora. Druga prośba i ACK dla innego
zadania/request_id są odrzucane.

`acknowledge` wymaga od przyszłej, zaufanej integracji wykonawcy
**dwóch oddzielnych dowodów:** Worker faktycznie zatrzymał inference
oraz checkpoint został zweryfikowany i trwale zapisany. Zbyt późny
ACK po wygaśnięciu lease jest odrzucany. To wyłącznie rejestr
protokołu — **nie ma jeszcze transportu STOP/ACK ani rzeczywistego
checkpointu zdalnego wykonawcy**. Dlatego nawet faza
`acknowledged` **NIE zmienia** wpisu `worker_job_safety` z
`running` na `idle_confirmed` i nie pozwala uzyskać rezerwacji
aktualizacji. Trzeba dopiero w jednej transakcji uzgodnić trwały
checkpoint, zwolnienie dzierżawy bieżącego wykonawcy i oddzielną
blokadę przydziału dla aktualizacji.

Dzięki temu użytkownik może żądać aktualizacji także podczas
aktywnego zadania — wykonawca zatrzyma się przy najbliższym
bezpiecznym punkcie i po restarcie wznowi od checkpointu albo
rozpocznie to zadanie od nowa. W obecnym prototypie nie wolno
deklarować, że przerwanie lub wznowienie już działa.

## 2026-11-22 — żądanie pauzy blokuje wszystkie nowe przydziały

W rejestrze `DurableWorkerJobs` tabela
`worker_update_pause_requests` jest tworzona wraz z pozostałymi
strukturami bezpieczeństwa, dzięki czemu zarówno bieżący scheduler
(`assign`), jak i starszy kompatybilny (`record_assignment`)
sprawdzają tę samą blokadę **w obrębie `BEGIN IMMEDIATE`**.
Wyścig „żądanie pauzy ↔ zakończenie partii ↔ nowy przydział” ma
zatem jednoznaczny wynik: albo nowe zadanie zostanie przydzielone,
zanim prośba mogła zostać zaakceptowana, albo prośba wygra i
wówczas przydział zostaje zablokowany. Nie ma możliwości, aby
obie operacje wygrały jednocześnie.

Dopóki istnieje prośba `requested` lub `acknowledged`,
`reserve_update` również odmawia, a odczytowy
`update_safety_snapshot` zwraca `unknown`. To istotne, ponieważ
zadanie mogło zakończyć się podczas wysyłania STOP i ustawić
`idle_confirmed` — nie oznacza to jeszcze zakończenia całego
protokołu pauzy. Wymagane jest jawne uzgodnienie i `clear`;
restart koordynatora nie usuwa prośby.

Te zabezpieczenia są już w bazie danych i mają testy wyścigów.
Faktyczny STOP/checkpoint/ACK wykonawcy i późniejsze wznowienie
nadal wymagają podłączenia do transportu Workera.

## 2026-11-22 — atomowe zapisanie zweryfikowanego checkpointu i rezerwacji

Dodano wewnętrzne `WorkerUpdatePause.finalize_verified_pause` oraz
trwałą tabelę `worker_update_resume`. Operacja używa `BEGIN IMMEDIATE`
i `synchronous=FULL`: potwierdzone przez zaufany transport zatrzymanie,
wcześniejszy ACK, aktywna dzierżawa, poprawny identyfikator job/request,
zaufany walidator checkpointu, jego kanoniczny JSON, SHA-256 i blokada
aktualizacji trafiają do jednego zatwierdzenia. Stan zadania zmienia się
z `running` na **`unknown`**, nie na `idle_confirmed`.
Przypadkowy nowy przydział nie jest możliwy, ponieważ istnieje hold
i nadal obecne żądanie pauzy.

Checkpoint jest ograniczony do 1 MiB, ma wersjonowany schemat,
identyfikator zadania i tryb `resume` / `restart`; po zapisie
jest odczytywany w transakcji, porównywany i haszowany. Odczyt po
restarcie ponownie weryfikuje SHA-256. Zepsuty checkpoint
blokuje wznowienie; zwykłe zwolnienie hold **nie usuwa** checkpointu,
a `clear` odrzuca czyszczenie przy oczekującym stanie wznowienia.

**Granica zaufania:** SHA-256 chroni spójność lokalnego zapisu, nie
uwierzytelnia Workera ani nie waliduje sensu stanu modelu. Operacja
jest wewnętrznym API koordynatora wymagającym zaufanego
`checkpoint_verifier` i potwierdzenia STOP z prawdziwego transportu.
Nie dodano publicznego endpointu, przycisku ani bezwarunkowego
`checkpoint_verified=True`. Istniejący prototyp Workera jeszcze nie
dostarcza takich dowodów, więc mechanizm nie jest aktywowany produkcyjnie.

Dalszy krok: podłączyć transport STOP/checkpoint/ACK oraz
weryfikator domenowy (np. legalną historię ruchów szachowych),
potem odczyt checkpointu i kontrolowane wznowienie bez podwójnych
wyników. Nadal nie uruchamiać instalatora APK bez zgody Androida.

## 2026-11-22 — blokada osieroconego checkpointu po aktualizacji

Tabela `worker_update_resume` jest współdzielona od inicjalizacji
`DurableWorkerJobs`. Zarówno nowe, jak i kompatybilne API przydziału
zadań oraz `reserve_update` sprawdzają ją **w tej samej transakcji**,
również wtedy, gdy blokada instalatora została już zwolniona i
ktoś usunął oddzielny wpis pauzy. `update_safety_snapshot` nie
ogłasza bezczynności, jeśli istnieje zapis przerwanego zadania.
Nie ma automatycznego „wznawiam” bez dowodu, że Worker powrócił
do działania, i nie ma usuwania checkpointu po samym zakończeniu
instalacji.

Dodano test, który celowo usuwa wpis pauzy i sztucznie ustawia stare
`idle_confirmed` po zwolnieniu hold. Obie ścieżki schedulera nadal
odmawiają przydziału, a zapis checkpointu przetrwał. Ostateczne
wznowienie wymaga osobnego zaufanego protokołu dispatcher–Worker,
kontroli wersji i idempotencji wyników.

## 2026-11-22 — jednorazowe uzgodnienie wznowienia po aktualizacji

Wewnętrzna metoda `WorkerUpdatePause.reconcile_resume` umożliwia
zaufanemu dispatcherowi dopięcie wznowienia w jednej transakcji
SQLite (`BEGIN IMMEDIATE`, `synchronous=FULL`).

Warunki: zakończona i zwolniona blokada instalacji, zgodne
`worker_id`/`job_id`/`request_id`/`checkpoint_sha256`,
nienaruszony checkpoint oraz jego ponowna weryfikacja domenowa,
uzgodniony stan `unknown` Workera, aktualny ACK gotowości
i **potwierdzenie przyjęcia dokładnie tego zadania do wznowienia**
przez rzeczywistego Workera. Dopiero wtedy w jednej transakcji
powstaje dzierżawa `running` dla **tego samego job_id** i usuwane są
zapisy `worker_update_resume` oraz pauzy. Ponowne wykonanie tej
operacji lub wyścig dwóch dispatcherów odrzuca drugi zapis.
Nieznany lub zepsuty checkpoint zachowuje blokadę.

**To API domenowe, jeszcze nie pełna obsługa sieciowa.** Ani Android
Worker, ani koordynator nie wystawiają jeszcze działającego protokołu
akceptacji resume/STOP/checkpoint; nie dodano automatycznego wywołania
tej metody ani ścieżki publicznego HTTP. Dowody gotowości muszą być
otrzymane z zaufanego transportu i zweryfikowane, nie należy
przekazywać do metody surowych flag z żądania klienta.
Po awarii między zaakceptowaniem zadania przez urządzenie a zapisem
koordynatora konieczne będzie rozstrzygnięcie identyfikatora
przyjęcia (idempotentny protokół), zanim automatyczne wznowienie
zostanie włączone.

## 2026-11-22 — uwierzytelniony kanał odczytu poleceń STOP

Koordynator udostępnia `POST /api/remote-workers/update-pause-poll`
tylko po uwierzytelnieniu oryginalnym tokenem maszyny i
sprawdzeniu losowego nonce (32 małe znaki hex). Endpoint niczego
nie zmienia: odpowiedź zawiera `instruction.action` równe
`none`, `stop_checkpoint` (`job_id` i niezmienny
`request_id`), `await_reconciliation` albo `blocked`.
Polecenie STOP jest zwracane tylko wtedy, gdy nadal istnieje
`requested` i aktywna zgodna dzierżawa tego samego zadania.
Wielokrotny odczyt zwraca to samo `request_id`: wykonawca
powinien potraktować je idempotentnie. W razie niezgodności stanu
lub upływu dzierżawy nie otrzymuje nowego polecenia, a blokada
pauzy pozostaje w bazie. Każda odpowiedź ma
`install_allowed=false`.

Natywny Rust ma niewystawioną do webview funkcję
`poll_pause_instruction`, która stosuje HTTPS, brak przekierowań,
token maszyny, losowe nonce i ścisłą walidację każdej odpowiedzi.
Nie przyjmuje dowolnych poleceń, nie wstrzymuje obliczeń samodzielnie,
nie generuje ACK i nie uruchamia instalatora.

**To początek transportu polecenia od koordynatora do Workera.**
Nadal trzeba podłączyć aktywne odpytywanie przez rzeczywistego
wykonawcę, weryfikowany trwały checkpoint i transport jego ACK.
Nie należy wywoływać `finalize_verified_pause` na podstawie
samego pollingu. Testy HTTP i Rust obejmują nonce, tożsamość,
powtarzalność polecenia, wygasłą dzierżawę oraz fałszywe twierdzenia
klienta o zakończonym STOP.

## 2026-11-22 — połączenie poleceń STOP z natywną aplikacją

Komenda Tauri `poll_worker_update_pause` wykonuje faktyczny,
uwierzytelniony odczyt polecenia `update-pause-poll` przez HTTPS.
Token Workera pozostaje wyłącznie w natywnym systemowym magazynie;
JavaScript przekazuje tylko jawny adres koordynatora i identyfikator
Workera. Rust weryfikuje odpowiedź powiązaną z losowym nonce,
tożsamością Workera i koordynatorem. Jeśli istnieje lokalny
nieuzgodniony hold, żądanie nie zostanie wysłane.

W widoku aktualizacji dodano przycisk „Sprawdź polecenie STOP z Areny”.
Można nim zobaczyć stany `none`, `stop_checkpoint`,
`await_reconciliation` i `blocked` bez wypisywania
`request_id`, sekretu lub danych checkpointu do webview.

**Zakres jest celowo ograniczony:** to rzeczywisty transport
polecenia, ale nie rzeczywiste zatrzymanie inference. Przy obecnym
prototypowym Workerze sterowanie obliczeniami pozostaje niedostępne.
Nawet gdy koordynator zwróci `stop_checkpoint`, aplikacja tylko
pokazuje żądanie — nie odsyła ACK i nie zwalnia blokady. Nie
dodano systemowej instalacji APK ani przyznawania zgody na nią.
Żeby ukończyć całość, trzeba podłączyć działający silnik zadań,
jego sprawdzony checkpoint i idempotentny protokół STOP/RESUME;
sam odczyt po HTTPS nie jest wystarczającym dowodem.
