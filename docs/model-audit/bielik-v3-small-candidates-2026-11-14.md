# Bielik v3 Small — wstępny audyt kandydatów GGUF

Data przeglądu: 2026-11-19 (weryfikacja HF API)
Zakres: wyłącznie publiczne karty modeli i indeksy plików Hugging Face.
Status obu kandydatów: `review_required`, `license_review=pending`, `technical_readiness=not_evaluated`, `revision_confirmed=false`, `scope_formats=[]`, `admission_status=not_admitted`.

## Ważne ograniczenia

- Nie pobierano wag, plików GGUF ani shardów.
- Nie uruchamiano kodu, modelu ani inferencji.
- Nie zmieniano katalogu kandydatów, manifestu modeli, allowlisty, bazy produkcyjnej, rosterów, wyników ani stanu turniejów.
- Nie jest to opinia prawna ani decyzja o dopuszczeniu.
- 2026-11-19 ponownie pobrano publiczne metadane przez Hugging Face API dla wszystkich czterech repozytoriów (dwa oficjalne SpeakLeash i dwa oddzielne second-state). Dla każdego `GET /api/models/{repo}` zwrócił SHA identyczne z zapisanym SHA commitu; dodatkowo `GET /api/models/{repo}/revision/{sha}` zwrócił dokładnie tę samą rewizję. Indeksy plików nie zawierają LICENSE/COPYING w żadnym z czterech repozytoriów. To potwierdza aktualne rewizje i widoczny indeks, ale nie audyt każdego artefaktu ani praw do danych treningowych; `revision_confirmed` pozostaje `false` do decyzji operatora.

## Kandydat A — Bielik 1.5B v3.0 Instruct GGUF

Repozytorium: https://huggingface.co/speakleash/Bielik-1.5B-v3.0-Instruct-GGUF

### Fakty ze źródeł

- Karta repozytorium GGUF deklaruje `apache-2.0`, oznacza model jako GGUF / Polish / text-generation i opisuje go jako kwantyzację Bielik-1.5B-v3.0-Instruct.
- Karta podaje SpeakLeash i ACK Cyfronet AGH jako autorów/rozwijających, język polski oraz Apache 2.0.
- Oficjalna karta bazowego modelu Instruct również deklaruje Apache-2.0; opisuje model jako fine-tuning Bielik-1.5B-v2.
- Widoczny indeks plików oficjalnego repozytorium GGUF pokazuje `Bielik-1.5B-v3.0-Instruct.Q8_0.gguf` (1.7 GB), wariant FP16 (3.2 GB), `.gitattributes` i README. Nie pokazuje osobnego pliku LICENSE/COPYING.
- README wspomina nazwę wariantu Q4_K_M w przykładzie konfiguracji, ale Q4_K_M nie jest widoczny w aktualnym indeksie plików oficjalnego repozytorium.
- Oddzielne repozytorium konwertujące `second-state/Bielik-1.5B-v3.0-Instruct-GGUF` publikuje zestawienie Q4_K_M ok. 973 MB. To inne repozytorium i inny łańcuch pochodzenia; nie wolno traktować tego pliku jako artefaktu SpeakLeash ani przenosić jego rozmiaru/SHA na repozytorium SpeakLeash.

### Ocena i luki

- Deklaracja licencji modelu i karty GGUF: Apache-2.0.
- Niezależne potwierdzenie tekstem LICENSE dla dokładnej rewizji GGUF: brak.
- SHA aktualnego `main` i odpowiedzi API dla przypiętej rewizji: `bb7ba70f60f2eee48d46857966ba3cf91bccb260` (zgodność 1:1, API 2026-11-19; indeks 4 plików, brak LICENSE/COPYING, brak Q4_K_M).
- Dokładny artefakt Q4_K_M w oficjalnym repozytorium: niepotwierdzony.
- Raport techniczny Bielik v3 opisuje korpus treningowy na poziomie ogólnym; ta wstępna kontrola nie ustala praw do każdego źródłowego dokumentu ani pełnej proweniencji danych.
- Wniosek: kandydat ma obiecującą deklarację licencji, ale dowody nie wystarczają do statusu `verified_permissive`.

## Kandydat B — Bielik 4.5B v3.0 Instruct GGUF

Repozytorium: https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF

### Fakty ze źródeł

- Karta repozytorium GGUF deklaruje `apache-2.0`, oznacza model jako GGUF / Polish / text-generation i opisuje go jako kwantyzację Bielik-4.5B-v3.0-Instruct.
- Karta podaje SpeakLeash i ACK Cyfronet AGH jako autorów/rozwijających, język polski oraz Apache 2.0.
- Oficjalna karta bazowego modelu Instruct deklaruje Apache-2.0. Opisuje model jako model instrukcyjny Bielik-4.5B-v3; karta bazowa wskazuje trening na polskich korpusach SpeakLeash oraz części Common Crawl.
- Widoczny indeks plików oficjalnego repozytorium GGUF pokazuje wariant Q8_0 (5.06 GB), wariant FP16 (9.52 GB), `.gitattributes` i README. Nie pokazuje osobnego pliku LICENSE/COPYING.
- README wspomina Q4_K_M w przykładzie konfiguracji, ale Q4_K_M nie jest widoczny w aktualnym indeksie plików oficjalnego repozytorium.
- Oddzielne repozytorium konwertujące `second-state/Bielik-4.5B-v3.0-Instruct-GGUF` publikuje Q4_K_M ok. 2.88 GB. To inne repozytorium i inny łańcuch pochodzenia; nie wolno przypisywać tego pliku wydawcy SpeakLeash.
- Raport techniczny Bielik v3 opisuje rodzinę 1.5B i 4.5B oraz trening na dużym korpusie polskim. Nie jest dokumentem rozstrzygającym licencje wszystkich źródłowych danych.

### Ocena i luki

- Deklaracja licencji modelu i karty GGUF: Apache-2.0.
- Niezależne potwierdzenie tekstem LICENSE dla dokładnej rewizji GGUF: brak.
- SHA aktualnego `main` i odpowiedzi API dla przypiętej rewizji: `c80ffe0e154fab9f7af170993a16d6f8acc83903` (zgodność 1:1, API 2026-11-19; indeks 4 plików, brak LICENSE/COPYING, brak Q4_K_M).
- Dokładny artefakt Q4_K_M w oficjalnym repozytorium: niepotwierdzony.
- Prawa do wszystkich elementów korpusu treningowego nie zostały niezależnie przeanalizowane.
- Wniosek: kandydat ma obiecującą deklarację licencji, ale dowody nie wystarczają do statusu `verified_permissive`.

## Porównanie artefaktów widocznych publicznie

| Model | Oficjalne repozytorium SpeakLeash | Q4_K_M w osobnym repozytorium konwertującym | Wniosek |
|---|---|---|---|
| 1.5B | Q8_0 1.7 GB; FP16 3.2 GB | ok. 973 MB | Mniejszy kandydat; Q4_K_M nie jest potwierdzony w oficjalnym repo |
| 4.5B | Q8_0 5.06 GB; FP16 9.52 GB | ok. 2.88 GB | Cięższy; Q4_K_M pochodzi z oddzielnego repo |

Rozmiar pliku na dysku nie jest wymaganiem RAM. Żadna wartość w tabeli nie potwierdza gotowości runtime ani zgodności z limitem pamięci VPS.

## Proponowane wartości formularza review

Dla obu kandydatów, do czasu uzupełnienia dowodów:

- `license_id=apache-2.0` — wyłącznie jako deklaracja źródła.
- `license_review=pending`.
- `technical_readiness=not_evaluated`.
- `revision_confirmed=false`.
- `proposed_status=review_required`.
- `scope_formats=[]`.
- `block_reason`: brak pełnej przypiętej rewizji GGUF, brak niezależnego tekstu LICENSE/COPYING dla dokładnej rewizji oraz niezweryfikowana proweniencja danych treningowych.
- `admission_status=not_admitted`; bez zmian allowlisty.

## Wymagane kolejne dowody (bez pobierania wag)

1. [Wykonano 2026-11-19] Zweryfikowano przez Hugging Face API aktualny SHA `main` oraz odpowiedź dla każdej przypiętej rewizji — wszystkie cztery SHA są zgodne.
2. [Wykonano 2026-11-19] Ponownie odczytano indeksy plików i deklaracje licencji; żadne z czterech repozytoriów nie zawiera LICENSE/COPYING. Oficjalne SpeakLeash nadal nie publikuje Q4_K_M w indeksie; oba oddzielne repozytoria second-state mają odpowiedni plik Q4_K_M.
3. Pozostaje ustalić, czy Q4_K_M ma być kandydatem z repo SpeakLeash, czy osobnym artefaktem z repozytorium konwertującego. Nie mieszać tych identyfikatorów.
4. Przypiąć rewizje upstream modeli bazowych i przeanalizować odpowiednie warunki oraz znane informacje o korpusie treningowym.
5. Dopiero po uzupełnieniu dowodów przedstawić człowiekowi podsumowanie zmian i decyzję do zatwierdzenia.

## Źródła

### Oficjalne źródła SpeakLeash
- GGUF 1.5B: https://huggingface.co/speakleash/Bielik-1.5B-v3.0-Instruct-GGUF
- Indeks plików GGUF 1.5B: https://huggingface.co/speakleash/Bielik-1.5B-v3.0-Instruct-GGUF/tree/main
- Bazowy Instruct 1.5B: https://huggingface.co/speakleash/Bielik-1.5B-v3.0-Instruct
- GGUF 4.5B: https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF
- Indeks plików GGUF 4.5B: https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF/tree/main
- Bazowy Instruct 4.5B: https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct
- Bazowy Bielik 4.5B v3: https://huggingface.co/speakleash/Bielik-4.5B-v3

### Źródła porównawcze — osobne repozytoria konwertujące, nie SpeakLeash
- Q4_K_M 1.5B: https://huggingface.co/second-state/Bielik-1.5B-v3.0-Instruct-GGUF — widoczny commit README: `6c316d2be07dee472901150c3f3e9d4f725a4706`.
- Q4_K_M 4.5B: https://huggingface.co/second-state/Bielik-4.5B-v3.0-Instruct-GGUF — widoczny commit README: `5ed9534f824c7c184c21320f4d339e75569d0987`.

### Pełne SHA obserwowanych commitów repozytoriów oficjalnych SpeakLeash
- GGUF 1.5B: `bb7ba70f60f2eee48d46857966ba3cf91bccb260` — https://huggingface.co/speakleash/Bielik-1.5B-v3.0-Instruct-GGUF/commit/bb7ba70f60f2eee48d46857966ba3cf91bccb260
- GGUF 4.5B: `c80ffe0e154fab9f7af170993a16d6f8acc83903` — https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF/commit/c80ffe0e154fab9f7af170993a16d6f8acc83903

### Raport techniczny
- Bielik v3 Small: https://arxiv.org/abs/2505.02550
