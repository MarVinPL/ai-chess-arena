# SmolLM2-360M — bezpieczny plan eksperymentalnego douczania

Status: **dataset v2 i lokalny pipeline przygotowane; trening/inference niewykonane**
Issue: #133
Model bazowy: `HuggingFaceTB/SmolLM2-360M-Instruct`
Kandydat GGUF w Arenie: `QuantFactory/SmolLM2-360M-Instruct-GGUF`

## Dane

### Seed v1

- 8 syntetycznych pozycji K+H vs K z jednym matem w jednym.
- Generator wylicza etykietę przez `python-chess`; odrzuca nielegalne FEN-y, duplikaty i pozycje bez dokładnie jednego ruchu matującego.
- Manifest: `datasets/chess_sft/manifest.json`; SHA-256: `219c4fdeadf941b9bc382d4bbd5e3e2b8f53262cf1488d599e524a985fa882d5`.
- To wyłącznie smoke-test formatu i walidatora.

### Dataset v2

- 100 samodzielnie wygenerowanych pozycji; CC0-1.0; bez zewnętrznych partii, komentarzy ani korpusów.
- SHA-256: `55096dfff15ddd1f7f9394f229593db5d21ece70170ea70bf5777d20a23e4ea6`.
- Podział bez przecieku grup źródłowych: train 80, validation 10, test 10; dokładny FEN deduplikowany globalnie.
- Zadania: 8 matów w końcówce, 4 wybory promocji, 24 unikalne bicia, 12 unikalnych roszowań, 45 unikalnych szachów i 7 jedynych odpowiedzi na szacha.
- Każdy rekord zawiera FEN, pełną listę legalnych ruchów, `best_move`, instrukcję zadania, regułę etykiety, metodę walidacji, split, grupę źródłową i licencję.
- Ważne: `best_move` oznacza jedyny legalny ruch spełniający regułę konkretnego zadania. To nie jest ocena globalnie najlepszego ruchu przez silnik szachowy; manifest jawnie zapisuje `engine_reference=null`.
- Generator jest deterministyczny i offline: `scripts/build_smol_chess_dataset_v2.py`.
- Przygotowanie ChatML/messages JSONL bez ładowania modelu: `scripts/prepare_smol_chess_sft.py`.
- Manifest datasetu: `datasets/chess_sft/manifest-v2.json`.
- Manifest przygotowanych splitów i ich SHA-256: `datasets/chess_sft/prepared-v2/manifest.json`.

## Pipeline treningu i ewaluacji

- `scripts/preflight_smol_chess_training.py` sprawdza wyłącznie lokalne pliki, dostępny RAM i wolne miejsce. Nie pobiera ani nie ładuje modelu.
- `scripts/train_smol_chess_lora.py` przygotowano jako CPU-only LoRA, z lokalnym `base-model-dir`, `local_files_only=True`, trybem offline, oddzielnym katalogiem adaptera i stratą liczona na odpowiedzi asystenta. Domyślnie wykonuje wyłącznie dry-run. Trening wymaga dwóch jawnych flag: `--execute --confirm-local-offline-training`.
- `scripts/evaluate_smol_chess_lora.py` ocenia wyłącznie odłożony split testowy, raportując legalność ruchu, trafność etykiety i czas inferencji. Domyślnie wykonuje wyłącznie dry-run; inference wymaga `--execute --confirm-local-inference`.
- Opcjonalne biblioteki treningowe (`torch`, `transformers`, `peft`) nie są instalowane ani pobierane w ramach tego PR. Skrypty nie pobierają wag bazowych ani nie korzystają z zewnętrznego API.
- Oryginalne wagi bazowe i kandydat GGUF pozostają nietknięte; adapter jest osobnym artefaktem.

## Aktualny preflight VPS — 2026-11-19

- Dostępny RAM: `1,608,564,736` B (ok. 1.50 GiB).
- Wolne miejsce: `1,616,228,352` B (ok. 1.50 GiB).
- Bez lokalnego snapshotu Transformers: `base-model-dir` nie został podany.
- Domyślne progi bezpieczeństwa preflight: 8 GiB RAM i 4 GiB wolnego miejsca.
- Wynik: **BLOCKED** — brak lokalnego snapshotu, RAM poniżej progu i dysk poniżej progu.
- Nie instalowano runtime treningowego, nie pobierano wag, nie uruchamiano modelu, treningu ani inferencji.

## Granice i dalsze bramki

1. Dataset v2 jest małym, syntetycznym zbiorem do eksperymentu z instrukcjami i legalnością; nie dowodzi poprawy siły gry ani jakości strategii.
2. Aby rozszerzyć dane o mat w 2, obronę przed matem i taktykę, potrzebny będzie jawnie wersjonowany silnik referencyjny z parametrami i głębokością. Nie wpisywać takich etykiet na podstawie przypuszczeń.
3. Przed treningiem wymagany jest kompletny lokalny snapshot Transformers, osobny preflight na środowisku z wystarczającą pamięcią oraz osobna zgoda właściciela na uruchomienie treningu.
4. Przed ewaluacją wymagane jest osobne potwierdzenie inference. Test odbywa się na identycznym, odłożonym zbiorze testowym dla bazy i adaptera.
5. Wyniki eksperymentu nie zmieniają allowlisty, admission, rosteru ani stanu turniejów.
6. Bez płatnych API, płatnych GPU, usług chmurowych z naliczaniem opłat ani niejasnych licencyjnie danych.
7. Chroniony katalog `/home/debian/ai-agent/models`, produkcyjna DB, usługi i turnieje pozostają nietknięte.

## Kryteria wyników

Raport po osobno zatwierdzonym uruchomieniu ma rozdzielać: legal move rate, task-target accuracy, wyniki według rodzaju zadania, czas inferencji oraz metryki train/validation. Nie wolno przedstawiać trafności etykiet syntetycznych jako siły gry lub rankingu ELO.
