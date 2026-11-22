# SmolLM2-360M — bezpieczny plan eksperymentalnego douczania

Status: **ARCHIWUM — część treningowa anulowana decyzją właściciela 2026-11-19; adapter nie został utworzony**
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

## Zachowany pipeline — tryb archiwalny (bez możliwości wykonania)

- `scripts/preflight_smol_chess_training.py` sprawdza wyłącznie lokalne pliki, dostępny RAM i wolne miejsce. Nie pobiera ani nie ładuje modelu.
- `scripts/train_smol_chess_lora.py` pozostaje jako dokumentacja prototypu i obsługuje wyłącznie dry-run; próba `--execute` kończy się komunikatem o anulowaniu.
- `scripts/evaluate_smol_chess_lora.py` pozostaje jako dokumentacja planowanej ewaluacji; obsługuje wyłącznie dry-run, a próba inference jest blokowana, ponieważ adapter nie istnieje.
- Opcjonalne biblioteki treningowe (`torch`, `transformers`, `peft`) nie są instalowane ani pobierane w ramach tego PR. Skrypty nie pobierają wag bazowych ani nie korzystają z zewnętrznego API.
- Oryginalne wagi bazowe i kandydat GGUF pozostają nietknięte; adapter jest osobnym artefaktem.

## Ostateczny preflight i decyzja — 2026-11-19

- Ostatni odczyt: dostępny RAM ok. `1.53 GiB`; wolne miejsce na `/` ok. `21.8 GiB`.
- Katalog wyjściowy na `/home/debian` usuwał wcześniejszą pozorną blokadę dyskową związaną z ograniczonym `/tmp`.
- Brak lokalnego snapshotu Transformers oraz RAM poniżej progu bezpieczeństwa 8 GiB.
- Wynik: **SKIPPED_BLOCKED** dla treningu na obecnym VPS; nie jest to awaria runtime modelu.
- Decyzją właściciela z 2026-11-19 część treningowa i dalsze próby optymalizacji zostały anulowane. Nie pobierano wag ani bibliotek, nie ładowano modelu i nie uruchamiano treningu/inferencji.

## Granice i dalsze bramki

1. Dataset v2 jest małym, syntetycznym zbiorem do eksperymentu z instrukcjami i legalnością; nie dowodzi poprawy siły gry ani jakości strategii.
2. Aby rozszerzyć dane o mat w 2, obronę przed matem i taktykę, potrzebny będzie jawnie wersjonowany silnik referencyjny z parametrami i głębokością. Nie wpisywać takich etykiet na podstawie przypuszczeń.
3. Trening LoRA został anulowany; nie wolno go ponawiać w ramach tego zadania.
4. Adapter: `NOT_CREATED`. Ewaluacja adaptera i porównanie baza–adapter: `NOT_APPLICABLE`.
5. Wyniki eksperymentu nie zmieniają allowlisty, admission, rosteru ani stanu turniejów.
6. Bez płatnych API, płatnych GPU, usług chmurowych z naliczaniem opłat ani niejasnych licencyjnie danych.
7. Chroniony katalog `/home/debian/ai-agent/models`, produkcyjna DB, usługi i turnieje pozostają nietknięte.

## Ostateczny zakres po anulowaniu

- Dataset v2, manifesty, przygotowane splity i skrypty dry-run pozostają jako archiwalny rezultat prac.
- Trening LoRA: `CANCELLED_BY_OWNER`. Adapter: `NOT_CREATED`. Ewaluacja adaptera i sparing: `NOT_APPLICABLE`.
- Zachowany historyczny test bazowego GGUF Q4_K_M jest odrębnym dowodem runtime; nie stanowi dowodu treningu ani poprawy siły gry.
- Nie zmieniać allowlisty, admission, rosteru ani wyników turniejów na podstawie tego eksperymentu.
