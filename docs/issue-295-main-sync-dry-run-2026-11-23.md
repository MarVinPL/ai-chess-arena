# PR #296: próba synchronizacji z main — 23.11.2026

## Stan wejściowy
- Branch: `feat/issue-295-native-model-cache`, HEAD `14aebc35010d8ad3a12bc120b9967730830fb967`.
- `main`: `12f8d17c21df778ef051c0b463e542e7a06342ec`.
- Rozbieżność: 12 commitów za `main`, 563 do przodu (stacked branches).
- Ostatni selektywny build Android na #296: [45253286954](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45253286954), **SUCCESS**.
- Czysta gałąź #296: 26 wybranych testów auth/Worker **PASS**.

## Izolowany, nieopublikowany merge

W tymczasowym klonie na VPS (`mktemp`, automatyczne usunięcie po testach) wykonano
`git merge --no-commit --no-ff origin/main`. Git wskazał osiem konfliktów:

1. `.github/workflows/android-worker-apk.yml`
2. `.github/workflows/worker-multiplatform.yml`
3. `index.html`
4. `remote_worker_inference_delivery.py`
5. `remote_worker_jobs.py`
6. `remote_worker_update_pause.py`
7. `server.py`
8. `tournament_checkpoint.py`

Dokonano **wyłącznie próbnego** uzgodnienia obu stron konfliktów:
zachowanie mechanizmu maintenance-holds, resume oraz kontroli checkpointów z `main`,
wspólne kroki Android CI, nowy interfejs `main`. Kolizja bramki
`/api/remote-workers/update-pause-poll` powodowała HTTP 503;
w próbie zastosowano selektywny dispatch (przeszedł w 58 testach).
Ta wersja nie jest jeszcze gotowa do zapisania na zdalnej gałęzi.

### Wyniki na próbnej integracji

- Testy ukierunkowane: **58 PASS**.
- Pełny `pytest`: **877 PASS, 15 FAIL, 1 skipped, 82 subtesty PASS**.
- 5 FAIL: `test_hf_candidate_downloads.py` — PermissionError przy próbie użycia
  `/var/lib/ai-chess-arena/hf-candidate-downloads` z nieuprzywilejowanego
  katalogu testowego VPS. Należy najpierw sprawdzić izolowane `ARENA_*`
  config/test fixtures, nie zmieniać uprawnień produkcji.
- 1 FAIL: `test_remote_worker_metrics.py` — nowy `index.html` z `main`
  nie zawiera linku `Zdalne komputery` z gałęzi #296.
- 1 FAIL: `test_remote_worker_phone_api_main.py` — konflikt priorytetu tras
  bramki phone API/inline Worker: test oczekuje 503, a otrzymuje 200.
- 1 FAIL: `test_worker_android_model_cache.py` — `main` usunął gałęziowy
  wariant triggera testowego push dla workflow Android.
- 6 FAIL: `test_worker_phone_checkpoint_end_to_end.py` oraz
  `test_worker_phone_move_provider.py` — nowe zabezpieczenia `main`
  dotyczące `side_to_move` i metadanych checkpointów odrzucają obecny
  format wytwarzany przez stos #296. Nie osłabiać walidatora bez audytu
  koordynatora; poprawić konstrukcję checkpointu / testy end-to-end.
- 1 FAIL: `test_worker_single_auth_route_pr296.py` — przy połączonym
  routingu mock `WorkerTokenStore.authenticate` nie jest wywoływany;
  należy zidentyfikować priorytet obsługi przed punktem auth.

## Wymagane działania

- [ ] Ujednolicić routing phone Worker (gate/feature flag, STOP/ACK,
      pojedyncza funkcja authenticate, źródło uprawnień i lease).
- [ ] Uzgodnić atomowy checkpoint CAS z wymaganiami schematu v2,
      `side_to_move` i niezmienialnych metadanych — bez obejścia kontroli.
- [ ] Zachować link `Zdalne komputery` w nowym interfejsie.
- [ ] Weryfikować CI Android bez ujawniania sekretów podpisu.
- [ ] Uruchomić testy HF w odizolowanym katalogu (bez chmod produkcyjnego).
- [ ] Dopiero po pełnym PASS wykonać rzeczywistą synchronizację branch i push.
- [ ] Zintegrować #296 z #316 przez jawny rejestr Worker routes,
      następnie staging STOP/ACK i rollback.
- [ ] Utrzymać #296 oraz #316 jako Draft i nie wdrażać produkcji.

**Decyzja: NO-GO dla merge.** Wszystkie klony testowe zostały usunięte.
Nie zmieniano serwisów VPS, flag produkcyjnych, danych ani `main`.

## Checkpoint compatibility fix — second isolated merge check

Commits on #296:
- `609bc072`: remote result commits now preserve immutable tournament
  metadata and validate `side_to_move` against the actual chess board turn;
- `66873373` and `02b53935`: update test fixtures for explicit board turn;
- `9e1ed4af`: use the stricter current-main transaction/CAS validation
  for `tournament_checkpoint.save_in_transaction`;
- `e28df81d`: negative tests verify forged side-to-move or player metadata
  cannot be committed and do not release the Worker lease.

Both unmerged and merged test copies were made under temporary directories
with cleanup traps; no production deployment and no merge commit published.

- Original PR #296: **18 selected tests PASS**.
- Isolated merge with current main, manually reconciled: **51 selected
  checkpoint/update-safety tests PASS**. Git conflict count decreased
  from 8 to **7 files**.
- Full isolated merged suite: **885 PASS, 9 FAIL, 1 skipped**, 82 subtests PASS.
  Previous attempt: 877 PASS, 15 FAIL.
- All **six checkpoint/lease integration failures** from the prior run
  are resolved. Remaining failures: five local HF download directory
  permission errors, one stale panel link expectation, one phone API
  feature-gate conflict, one Android workflow branch-trigger expectation,
  one full-suite-only Worker auth test failure (selected tests PASS).

Do **not** relax the validated CAS/turn/metadata checks or infer a production
success. Continue addressing remaining issues before synchronizing the remote
branch; PR #296 remains 12 commits behind main until an actual merge is
published, and approval for production is separate.
