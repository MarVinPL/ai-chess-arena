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
