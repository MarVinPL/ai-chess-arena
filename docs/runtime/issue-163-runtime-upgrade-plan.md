# Issue #163 — odseparowany runtime llama.cpp

Status: **plan techniczny + inertny kontrakt argv; brak builda, instalacji i uruchomienia**.

## Stan zastany

- Domyślny runtime pozostaje `/home/debian/ai-agent/bin/llamafile`, llamafile v0.10.6, SHA-256 `756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50`.
- Obecny runner (`engine_runner.py`) ma stały `API_BASE=http://127.0.0.1:8088`, wywołuje `/health` i `/v1/chat/completions`, uruchamia proces jako własny child, ma watchdog i zatrzymuje wyłącznie własny proces (`terminate`, potem `kill` po timeout).
- Aktualne parametry startowe obejmują host loopback, jeden slot, liczbę wątków i kontekst. Limit startu 90 s nie jest limitem czasu odpowiedzi; ruch HTTP ma `timeout=None` zgodnie z polityką turniejową.
- `api_base` jest wstrzykiwalne do klasy, ale port w argv jest obecnie na stałe `8088`. Kandydat z innym portem wymaga osobnego adaptera/konfiguracji, nie samej zmiany URL.
- VPS ma x86_64 Intel Haswell, 2 vCPU, AVX2/FMA/BMI2, ok. 3.7 GiB RAM, bez wykrytego GPU. Na hoście nie ma obecnie `cmake`/`ninja`; kompilacja na VPS jest wykluczona.

## Porównanie opcji

| Kryterium | Odseparowany llamafile build | Osobny upstream `llama-server` |
|---|---|---|
| Obsługa architektury | Zależy od synchronizacji projektu llamafile z upstream; oficjalne v0.10.6 zawiera b10441 | Bezpośrednio z upstream; Spark2.5 jest dodany w commit `3ad1ba7336986d98592d3e28cafd1a406715351f` |
| Pakowanie | Pojedynczy, wygodny plik; jednak build system Cosmopolitan i proces synchronizacji to dodatkowa warstwa | Standardowy CMake build i osobny serwer; większa liczba artefaktów/zależności |
| Integracja z runnerem | CLI jest podobne, ale trzeba pinować konkretny build i sprawdzić wrapper shell/ELF | OpenAI-compatible HTTP API jest zbieżne; wymaga adaptera argv i osobnego portu |
| Ryzyko utrzymania | Fork/release może pozostawać w tyle za upstream | Mniej pośredników i prostsze przypięcie źródła, ale odpowiedzialność za paczkowanie zależności |
| Decyzja | Nie wybierać jako ścieżki pierwszej | **Preferowany kandydat do odseparowanego prototypu**, bez aktywacji |

Oficjalne llamafile v0.10.6 deklaruje aktualizację do llama.cpp b10441; nie osiąga minimalnego potwierdzonego poziomu b10830 dla Spark2.5. Nie zakładamy, że kolejne wydanie llamafile już istnieje ani że sam numer wersji oznacza kompatybilność.

## Pinning i pochodzenie

- Minimalny potwierdzony feature floor dla `spark2_5`: llama.cpp b10830 (weryfikacja opisana przez użytkowników w upstream issue/ekosystemie).
- Commit wprowadzający obsługę: `ggml-org/llama.cpp@3ad1ba7336986d98592d3e28cafd1a406715351f` (PR #27868, 2026-09-06). To **pin dowodu wprowadzenia architektury**, nie automatyczna rekomendacja wdrożenia dokładnie tego historycznego commitu.
- Przed jakimkolwiek buildem trzeba wskazać jeden pełny commit/tag upstream co najmniej b10830, zweryfikować, że zawiera powyższy commit, i zachować wynik `git rev-parse`, podpis/attestation jeśli dostępne, SHA-256 artefaktów oraz manifest zależności.
- Nie używać ruchomego `master`, `latest`, niezaufanych forków ani nieprzypiętych kontenerów.
- Build powinien powstać poza produkcyjnym VPS (preferowane darmowe CI/runner), z zachowanym logiem kompilacji, SBOM (np. CycloneDX/SPDX), licencjami i checksumami. W tym Issue build nie jest wykonywany.

## Kontrakt kandydata

`runtime_upgrade_plan.py` definiuje wyłącznie inertny, testowalny profil upstream:

- binarium planowane w `/opt/ai-chess-arena/runtimes/llama.cpp-b10830/bin/llama-server`;
- API `http://127.0.0.1:8089` — osobny port, bez kolizji z aktywnym `8088`;
- loopback-only, jeden slot (`-np 1`), CPU-only (`-ngl 0`), tryb offline (`--offline`), brak Web UI i warmup, jeden plik GGUF;
- flagi CLI zostały sprawdzone w README upstream przypiętym do commitu wprowadzającego `spark2_5`;
- builder zwraca argv i **nie wykonuje** komendy, nie pobiera plików i nie autoryzuje runtime.

Kontrakt HTTP zweryfikowany testami na syntetycznych payloadach (bez serwera i wag); testy nie dowodzą jeszcze zgodności konkretnego binarium:

1. `GET /health` → HTTP 200 po gotowości;
2. `POST /v1/chat/completions` → JSON `choices[0].message.content` i opcjonalne `usage.completion_tokens`;
3. nieprawidłowy status/JSON/kształt odpowiedzi musi kończyć się błędem, nie ruchem domyślnym;
4. zatrzymanie dotyczy tylko procesu potomnego uruchomionego przez adapter; najpierw `terminate`, ograniczony `wait`, potem `kill`;
5. watchdog restartuje tylko własny proces, tylko jeśli nadal istnieje oczekiwany model; intencjonalne `stop` wyłącza restart;
6. timeout startu jest odrębny od czasu inferencji. Nie dodawać limitu czasu ruchu bez osobnej decyzji.

Profil nie jest importowany przez `engine_runner.py`, `server.py` ani ścieżkę turniejową. Domyślny runtime pozostaje bez zmian.

## Haswell i zasoby

- CPU ma AVX2, FMA i BMI2; brak podstaw do założenia AVX-512 lub GPU.
- Build musi mieć jawny, zachowawczy target x86-64 i runtime dispatch; nie używać `-march=native` na maszynie buildowej, jeśli artefakt ma działać na innym CPU. Osobny wariant Haswell może być rozważony dopiero po porównaniu flag i testach na tej samej klasie CPU.
- Sama obsługa `spark2_5` nie dowodzi, że model zmieści się w RAM/swap ani że inferencja będzie praktyczna. Każdy przyszły test wymaga osobnego preflight pamięci/dysku i uruchamiania najwyżej jednego modelu naraz.
- Nie dotykać `/home/debian/ai-agent/models`; artefakty kandydatów pozostają w kwarantannie.

## Testy i bramki

W tym PR wolno uruchamiać tylko testy jednostkowe/mockowane oraz statyczne kontrole. Testy sprawdzają argv, odpowiedź `GET /health`, minimalny kształt `POST /v1/chat/completions`, opcjonalne usage i odrzucanie błędnych statusów/JSON. Nie uruchamiać modelu, nie wykonywać inferencji, benchmarku ani turnieju. Nie pobierać/instalować binariów na VPS i nie kompilować na VPS.

Przed osobnym testem runtime wymagane będą: zaakceptowany PR, przypięty build i checksumy, osobna ścieżka binarium, potwierdzone API/CLI na sztucznym modelu testowym lub fixture bez chronionych wag, audyt SBOM/licencji, snapshot zasobów i osobna jawna zgoda na test jednego modelu. Instalacja/aktywacja/deploy wymaga odrębnej zgody.

## Rollback (przyszły, nie wykonywany)

Nowy runtime ma pozostać w swoim własnym katalogu. Nie podmieniać istniejącego binarium ani systemd. Rollback oznacza wyłączenie wyłącznie jawnie wskazanego profilu testowego i powrót do domyślnego profilu istniejącego runnera; zachować logi/checksumy. Brak automatycznego przełączenia po błędzie.

## Źródła

- [llamafile v0.10.6 release](https://github.com/mozilla-ai/llamafile/releases) — update do llama.cpp b10441.
- [llama.cpp commit wprowadzający Spark2.5](https://github.com/ggml-org/llama.cpp/commit/3ad1ba7336986d98592d3e28cafd1a406715351f).
- [llama.cpp server README at the Spark2.5 support commit](https://github.com/ggml-org/llama.cpp/blob/3ad1ba7336986d98592d3e28cafd1a406715351f/tools/server/README.md) — potwierdza `-m`, `--host`, `--port`, `-t`, `-c`, `-np`, `-ngl`, `--offline`, `--no-webui`, `--no-warmup`, `/health` i `/v1/chat/completions`.
- [Upstream Spark2.5 support discussion](https://github.com/ollama/ollama/issues/18195) — raport potwierdzający testy z oficjalnym buildem b10830.
