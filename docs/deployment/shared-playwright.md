# Współdzielone środowisko E2E na VPS

Zasób: użytkownik debian, /home/debian/.local/share/playwright-tools,
launcher /home/debian/.local/bin/playwright-run. Node.js 20.19.2,
Playwright 1.65.1, systemowy /usr/bin/chromium (zweryfikowano 156).
Bez serwisu sieciowego, dodatkowych przeglądarek i opłat licencyjnych.
Nie korzysta z instalacji Browser VPS.

## Odtworzenie

Z checkoutu repozytorium z zatwierdzonymi plikami:

    sh deploy/playwright-tools/install.sh
    "$HOME/.local/bin/playwright-run" "$HOME/.local/share/playwright-tools/smoke.cjs"

Wymagania: Node.js, npm, systemowy Chromium i działający sandbox.
Manifest oraz lockfile przypinają wersje; npm ci pomija skrypty instalacyjne.
Aktualizacje wersji wymagają PR, nowego lockfile i ponownego smoke testu.

## Użycie z innych projektów

    "$HOME/.local/bin/playwright-run" /ścieżka/do/testu.cjs

Skrypty CommonJS mogą używać require('playwright'). Launcher ustawia
NODE_PATH i PLAYWRIGHT_CHROMIUM_EXECUTABLE. Dla ESM należy importować
pakiet z jawnej ścieżki współdzielonego środowiska.
Uruchamiać chromium.launch z executablePath ze zmiennej środowiskowej,
headless:true i chromiumSandbox:true. Każdy test używa nowego BrowserContext,
a browser.close() wywołuje w finally. Testować na izolowanych danych,
nie na aktywnych turniejach. Nie zapisywać tokenów ani sesji do repozytorium.

## Porządkowanie

Screenshoty smoke testu są w pamięci. Testy projektowe mają usuwać własne
tymczasowe profile i artefakty po zakończeniu. Nie usuwać cache ani
instalacji innych projektów. Nie uruchamiać instalatora równocześnie
z testami korzystającymi ze wspólnego środowiska.
