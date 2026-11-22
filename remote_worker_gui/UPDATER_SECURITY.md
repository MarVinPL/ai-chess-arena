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
