# Audyt licencji modeli — 2026-11-14

## Status i zakres

To jest techniczny audyt dowodów dla wpisów 1–8 rejestru, zgodnie z aktualnym zakresem Issue #52. Wpisy 9–10 są wyłączone z kryterium ukończenia tego audytu i śledzone oddzielnie w Issue #71; pozostają `review_required` i nie są zatwierdzone do pobierania, uruchamiania, allowlisty ani udziału w turniejach. Nie jest to opinia prawna ani zgoda właściciela projektu na pobranie, uruchomienie, dystrybucję lub udział w turnieju.

Nie pobierano wag GGUF, nie uruchamiano inferencji, nie zmieniano produkcyjnej bazy, rosterów, wyników ani usług. Nie odczytywano współdzielonego katalogu modeli.

## Wyniki

| ID | Model / artefakt | Status audytu | Ustalenie |
|---:|---|---|---|
| 1 | Qwen2.5-0.5B-Instruct Q4_K_M GGUF | `verified_permissive` | Oficjalny pinned README i LICENSE są dostępne w tej samej rewizji; LFS SHA-256 dokładnego GGUF zgadza się z rejestrem, a przypięta licencja modelu bazowego również wskazuje Apache-2.0. |
| 2 | SmolLM2-360M-Instruct Q4_K_M GGUF | `verified_permissive` | Pinned README identyfikuje model bazowy i deklaruje Apache-2.0; przypięta karta upstream wprost odsyła do kanonicznego tekstu Apache License 2.0. |
| 3 | Qwen2-0.5B-Instruct Q4_K_M GGUF | `verified_permissive` | Pinned README identyfikuje model bazowy i deklaruje Apache-2.0; dokładna przypięta rewizja oficjalnego upstream zawiera pełny tekst Apache License 2.0. |
| 4 | Meta Llama 3.1 8B Instruct Q3_K_S GGUF | `verified_restricted` | Obowiązuje Llama 3.1 Community License oraz AUP Meta, z warunkami atrybucji, NOTICE, dystrybucji i dodatkowym progiem MAU. |
| 5 | Llama 3.2 3B Instruct UD-IQ3_XXS GGUF | `verified_restricted` | Obowiązuje Llama 3.2 Community License oraz AUP Meta, z warunkami atrybucji, NOTICE, dystrybucji i dodatkowym progiem MAU. |
| 6 | Bielik 11B v3.0 Instruct Q4_K_M GGUF | `verified_restricted` | Apache-2.0 jest uzupełnione oficjalnymi Terms of Use Bielik v3, w tym ograniczeniem wieku 18+ oraz dodatkowymi ograniczeniami użycia i przetwarzania danych. |
| 7 | Bielik 7B Instruct v0.1 Q3_K_S GGUF | `verified_restricted` | Pinned GGUF i model Instruct deklarują CC BY-NC 4.0. Obowiązują ograniczenie NonCommercial, atrybucja i oznaczenie modyfikacji. |
| 8 | Qwen3.8-27B UD-IQ1_S GGUF | `verified_permissive` | Pinned README identyfikuje oficjalny model bazowy; dokładna przypięta rewizja upstream zawiera pełny tekst Apache License 2.0. |

## Szczegółowe dowody

### 1. Qwen2.5-0.5B-Instruct Q4_K_M

- Repozytorium GGUF: `Qwen/Qwen2.5-0.5B-Instruct-GGUF`.
- Przypięta rewizja repozytorium: `9217f5db79a29953eb74d5343926648285ec7e67`.
- Plik: `qwen2.5-0.5b-instruct-q4_k_m.gguf`, 491,400,032 B.
- SHA-256 z manifestu i metadanych LFS: `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`; wartość jest zgodna z artefaktem w przypiętej rewizji.
- Pinned README z tej samej rewizji identyfikuje oficjalny model bazowy `Qwen/Qwen2.5-0.5B-Instruct`, opisuje oficjalne wydanie GGUF i deklaruje Apache-2.0.
- Repozytorium GGUF zawiera w tej rewizji pełny plik LICENSE Apache 2.0. Oficjalny model bazowy ma przypiętą rewizję `7ae557604adf67be50417f59c2c2f167def9a775` i również zawiera pełny tekst Apache-2.0.
- Wniosek: `verified_permissive` dla dokładnego GGUF i wskazanego zakresu licencji. Apache 2.0 wymaga m.in. zachowania kopii licencji, informacji o zmianach oraz odpowiednich notice/attribution przy redystrybucji. Status audytu nie jest zgodą właściciela projektu na pobranie ani uruchomienie.

Źródła:
- Pinned GGUF README: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/9217f5db79a29953eb74d5343926648285ec7e67/README.md
- Pinned GGUF LICENSE: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/9217f5db79a29953eb74d5343926648285ec7e67/LICENSE
- Pinned upstream base-model LICENSE: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/blob/7ae557604adf67be50417f59c2c2f167def9a775/LICENSE

### 2. SmolLM2-360M-Instruct Q4_K_M

- Repozytorium GGUF: `QuantFactory/SmolLM2-360M-Instruct-GGUF`.
- Rewizja artefaktu: `25eef036a8d245a5a188b1b45d4b240a39bc9f24`.
- Plik: `SmolLM2-360M-Instruct.Q4_K_M.gguf`, 270,590,592 B.
- SHA-256 z rejestru i LFS: `75c4346ef9e855ed630f80078a2430cf63aaca599e340360998a313070fcdc47`.
- Pinned README wskazuje `HuggingFaceTB/SmolLM2-360M-Instruct` jako źródło i deklaruje Apache-2.0.
- Oficjalna karta modelu bazowego na przypiętej rewizji `a10cc1512eabd3dde888204e902eca88bddb4951` deklaruje Apache-2.0.
- Karta modelu bazowego w dokładnej przypiętej rewizji wskazuje Apache 2.0 i zawiera bezpośredni link do kanonicznego tekstu licencji Apache. To stanowi dowód warunków upstream; nie znaleziono dodatkowych ograniczeń w sprawdzonych plikach.

Źródła:
- GGUF README (pinned): https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF/blob/25eef036a8d245a5a188b1b45d4b240a39bc9f24/README.md
- Model bazowy README (pinned): https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct/blob/a10cc1512eabd3dde888204e902eca88bddb4951/README.md
- Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0

### 3. Qwen2-0.5B-Instruct Q4_K_M

- Repozytorium GGUF: `Qwen/Qwen2-0.5B-Instruct-GGUF`.
- Rewizja artefaktu: `198f08841147e5196a6a69bd0053690fb1fd3857`.
- Plik: `qwen2-0_5b-instruct-q4_k_m.gguf`, 397,805,248 B.
- SHA-256 z rejestru i LFS: `f0a42bb979ca62b5e61f3bf924ab4b6a40aa091825ee7dcb4039949980ab81a8`.
- Pinned README deklaruje Apache-2.0, wskazuje `Qwen/Qwen2-0.5B-Instruct` i opisuje konwersję do GGUF przez llama.cpp.
- Oficjalny upstream ma przypiętą rewizję `c540970f9e29518b1d8f06ab8b24cba66ad77b6d` z pełnym plikiem LICENSE Apache-2.0.
- Wniosek: `verified_permissive` dla zidentyfikowanego artefaktu i warunków licencyjnych. Nie jest to zgoda na pobranie ani użycie.

Źródła:
- GGUF README (pinned): https://huggingface.co/Qwen/Qwen2-0.5B-Instruct-GGUF/blob/198f08841147e5196a6a69bd0053690fb1fd3857/README.md
- Upstream LICENSE (pinned): https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/blob/c540970f9e29518b1d8f06ab8b24cba66ad77b6d/LICENSE
- Upstream README (pinned): https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/blob/c540970f9e29518b1d8f06ab8b24cba66ad77b6d/README.md

### 4–5. Meta Llama

- Llama 3.1 GGUF README (pinned): https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/blob/bf5b95e96dac0462e2a09145ec66cae9a3f12067/README.md
- Llama 3.2 GGUF README (pinned): https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/blob/e7d0997e49c9cb00d88b4c1a6a16aa894b0bbc31/README.md
- Meta Llama 3.1 Community License: https://dev.meta.ai/llama/llama3_1/license
- Meta Llama 3.1 AUP: https://dev.meta.ai/llama/llama3_1/use-policy
- Meta Llama 3.2 Community License: https://dev.meta.ai/llama/llama3_2/license
- Meta Llama 3.2 AUP: https://dev.meta.ai/llama/llama3_2/use-policy

Warunki Meta nie są Apache-2.0 ani bezwarunkową licencją permissive. Audyt odnotowuje m.in. wymagania dotyczące oznaczeń, NOTICE, atrybucji, AUP oraz dodatkowy próg MAU z umowy.

### 6. Bielik 11B v3.0 Instruct

- Repozytorium GGUF: `speakleash/Bielik-11B-v3.0-Instruct-GGUF`.
- Rewizja artefaktu: `a68a2b6c7064a23446d9bb6f0cf3a051972c9ac7`.
- Plik: `Bielik-11B-v3.0-Instruct.Q4_K_M.gguf`, 6,724,051,584 B.
- SHA-256: `c16841621efe93c7c8ebf1b374709a96276f3741e649f83ed90131a7b5ad23a8`.
- Pinned README GGUF identyfikuje model Instruct i deklaruje Apache-2.0.
- Oficjalna karta modelu Instruct (rewizja `735bfee1125fe8b497ac2769de94822a11f77167`) wskazuje bazę `Bielik-11B-v3-Base-20250730`.
- Dodatkowe Terms of Use v1.1 z 31.07.2025 obejmują model bazowy i wersje fine-tuned. Dokument określa minimalny wiek 18 lat, wymaga zgodnego z prawem i etyką użycia oraz wprowadza dalsze ograniczenia, w tym zakaz przetwarzania danych osobowych.
- Wniosek: `verified_restricted`. Ograniczenie wieku jest warunkiem korzystania z modelu, a nie wyłącznie warunkiem usługi internetowej.

Źródła:
- GGUF README (pinned): https://huggingface.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF/blob/a68a2b6c7064a23446d9bb6f0cf3a051972c9ac7/README.md
- Instruct model card (pinned): https://huggingface.co/speakleash/Bielik-11B-v3.0-Instruct/blob/735bfee1125fe8b497ac2769de94822a11f77167/README.md
- Official Terms of Use v1.1: https://www.bielik.ai/en/terms/
- Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0

### 7. Bielik 7B Instruct v0.1

- Repozytorium GGUF: `speakleash/Bielik-7B-Instruct-v0.1-GGUF`.
- Rewizja artefaktu: `b482400d6d36fa02167bc32f5f7b2d368271b4a1`.
- Plik: `bielik-7b-instruct-v0.1.Q3_K_S.gguf`, 3,164,568,128 B.
- SHA-256: `b5a76329d1bbd28c2b3e95968d1b2b9c85d2c68d56b6708fde8200c6a6560fa9`.
- Pinned README GGUF wskazuje model Instruct; pinned upstream Instruct README (rewizja `ff00a0b55e9a64be87f9a8d2dcb65882f264aba4`) deklaruje CC BY-NC 4.0.
- Oficjalny tekst CC BY-NC 4.0 ogranicza wykorzystanie do celów niekomercyjnych i nakłada warunki atrybucji oraz oznaczania zmian. Bazowy model pretrainingowy ma odrębną deklarację Apache-2.0, ale nie znosi to bardziej restrykcyjnych warunków modelu Instruct, z którego powstała kwantyzacja.
- Wniosek: `verified_restricted`; nie klasyfikować jako permissive ani jako dopuszczonego do zastosowań komercyjnych.

Źródła:
- GGUF README (pinned): https://huggingface.co/speakleash/Bielik-7B-Instruct-v0.1-GGUF/blob/b482400d6d36fa02167bc32f5f7b2d368271b4a1/README.md
- Upstream Instruct README (pinned): https://huggingface.co/speakleash/Bielik-7B-Instruct-v0.1/blob/ff00a0b55e9a64be87f9a8d2dcb65882f264aba4/README.md
- CC BY-NC 4.0 Legal Code: https://creativecommons.org/licenses/by-nc/4.0/legalcode.en

### 8. Qwen3.8-27B

- Repozytorium GGUF: `unsloth/Qwen3.8-27B-GGUF`.
- Rewizja artefaktu: `4af51b3542f775b0a8e4b310bfb3f17e01c306c9`.
- Plik: `Qwen3.8-27B-UD-IQ1_S.gguf`, 6,192,222,208 B.
- SHA-256: `3895b6eaa91e705c06ad1938d16c22e86f073c6a67df86260a1da79be3d1f887`.
- Pinned README GGUF identyfikuje `Qwen/Qwen3.8-27B` jako bazę i deklaruje Apache-2.0.
- Oficjalne repozytorium Qwen ma przypiętą rewizję `37561816e66b5b9153b1b64c10968c18c1e6f4a0`, która zawiera pełny plik LICENSE Apache-2.0.
- Wniosek: `verified_permissive` dla zidentyfikowanego artefaktu i warunków licencyjnych. Nie jest to zgoda na pobranie ani użycie.

Źródła:
- GGUF README (pinned): https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/blob/4af51b3542f775b0a8e4b310bfb3f17e01c306c9/README.md
- Official Qwen LICENSE (pinned): https://huggingface.co/Qwen/Qwen3.8-27B/blob/37561816e66b5b9153b1b64c10968c18c1e6f4a0/LICENSE
- Official Qwen README (pinned): https://huggingface.co/Qwen/Qwen3.8-27B/blob/37561816e66b5b9153b1b64c10968c18c1e6f4a0/README.md

## Pozostałe blokady

- Wpisy 9 i 10 pozostają `review_required` i nie zostały zatwierdzone: ich pochodzenie/warunki wymagają odrębnego przeglądu. Nie należy interpretować samej deklaracji Apache-2.0 w metadanych pochodnego repozytorium jako wystarczającego dowodu ani dopuszczenia do użycia.
- Dwa pozostałe wpisy rejestru nie zostały objęte analizą w tym bezpiecznym zakresie. Ich statusy pozostają bez zmian i nie wolno traktować ich jako zatwierdzonych.
- Status audytu nie jest zgodą właściciela projektu. Import manifestu nadal wymusza `license_verified=0`; żadna zmiana w tym raporcie nie przyznaje uprawnień do pobrania, uruchomienia ani udziału w turnieju.
- Do zamknięcia Issue #52 wymagane jest rozstrzygnięcie wszystkich wpisów i ewentualna decyzja właściciela; przypadki niejasne muszą pozostać zablokowane.

## Zastrzeżenie

To techniczny przegląd publicznych warunków i dowodów, nie porada prawna. W razie komercyjnej dystrybucji, udostępniania wag lub niejasności co do warunków bazowych potrzebna jest odrębna decyzja właściciela projektu i — w razie potrzeby — konsultacja prawna.
