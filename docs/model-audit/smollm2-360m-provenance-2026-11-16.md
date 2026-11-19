# SmolLM2-360M-Instruct — korekta provenance danych treningowych

**Issue:** #139  
**Model registry ID:** 2  
**Artefakt GGUF:** `QuantFactory/SmolLM2-360M-Instruct-GGUF`  
**Przypięta rewizja artefaktu:** `25eef036a8d245a5a188b1b45d4b240a39bc9f24`  
**Plik:** `SmolLM2-360M-Instruct.Q4_K_M.gguf`  
**Rozmiar:** 270,590,592 B  
**SHA-256:** `75c4346ef9e855ed630f80078a2430cf63aaca599e340360998a313070fcdc47`  
**Data przeglądu:** 2026-11-16

## 1. Wniosek

Korekta rozdziela pipeline modelu 360M na pretraining, supervised fine-tuning (SFT) i direct preference optimization (DPO). Najważniejszym źródłem jest publikacja SmolLM2, sekcja 6, która opisuje warianty 135M/360M osobno od większego wariantu 1.7B.

Wynik pozostaje **`review_required`**. Publiczne materiały potwierdzają rodziny i etapy danych, ale nie zapewniają kompletnego, odtwarzalnego bill of materials dla dokładnego treningu: brak pełnego zestawu immutable dataset revisions, wszystkich konfiguracji/subsetów, filtrów i snapshotów wejściowych. Deklarowana licencja artefaktu/modelu Apache-2.0 nie rozstrzyga automatycznie praw do każdego źródłowego elementu danych.

To jest audyt dokumentacyjny publicznych metadanych, a nie opinia prawna ani zgoda na pobieranie, uruchamianie, redystrybucję lub dopuszczenie modelu.

## 2. Korekta wcześniejszego opisu

Poprzedni raport wymieniał ogólnie FineWeb-Edu, DCLM, The Stack i Argilla Synth-APIGen-v0.1. To zbyt szeroki opis dla konkretnego wariantu 360M.

- Model card 360M-Instruct opisuje rodzinę ogólnie: FineWeb-Edu, DCLM, The Stack, a dla instruction modelu także Synth-APIGen jako przykład datasetu opracowanego przez Argilla.
- Publikacja rozróżnia 360M/135M od 1.7B. Dla mniejszych modeli podaje: DCLM filtrowany klasyfikatorem FineWeb-Edu, Stack-Edu, InfiMM-WebMath, FineMath i Cosmopedia.
- Dla 360M/135M post-training publikacja wskazuje filtrowaną wersję SmolTalk do SFT oraz UltraFeedback do DPO.
- Karta `HuggingFaceTB/smol-smoltalk` mówi wprost, że ten podzbiór wykorzystano do budowy SmolLM2-360M-Instruct i SmolLM2-135M-Instruct, a następnie wykonano DPO na UltraFeedback.
- Nie ma wystarczającego dowodu, aby przypisać `Argilla/Synth-APIGen-v0.1` jako konkretny, bezpośredni składnik treningu wariantu 360M. Nie należy tego twierdzenia powtarzać jako potwierdzonego źródła dla ID 2.

## 3. Pipeline i dowody

### 3.1 Pretraining

Publikacja podaje, że SmolLM2-360M trenowano na 4T tokenów. W sekcji 6 autorzy opisują dla modeli 135M/360M:
- DCLM filtrowany FineWeb-Edu classifierem; usuwano próbki z wynikiem 0 i downsamplowano wyniki 1–2;
- Stack-Edu;
- InfiMM-WebMath;
- FineMath;
- Cosmopedia.

Ważne rozróżnienie: FineWeb-Edu jest tu wskazany jako źródło klasyfikatora/metody filtrowania DCLM; nie należy bez dodatkowego dowodu traktować go jako odrębnego, bezpośredniego korpusu wejściowego do każdego etapu 360M. Ogólny model card używa szerszego opisu.

| Składnik | Dowód użycia w 360M | Publicznie deklarowane warunki / ryzyka | Braki |
|---|---|---|---|
| DCLM filtrowany FineWeb-Edu classifierem | Publikacja, sekcja 6; karta `HuggingFaceTB/dclm-edu` deklaruje użycie do SmolLM2-135M/360M | DCLM-Edu karta deklaruje CC-BY-4.0. FineWeb-Edu karta deklaruje ODC-By 1.0 i odsyła do Common Crawl Terms of Use jako warunków źródła/filtracji | Nieustalony immutable snapshot i pełna konfiguracja filtrowania/ważenia zastosowana w finalnym treningu |
| Stack-Edu | Publikacja, sekcja 6, wymienia Stack-Edu jako składnik treningu 360M | Karta Stack-Edu mówi, że zawiera SWHIDs, a treść pobiera się z Software Heritage; licencje należy oceniać zgodnie z The Stack v2 | Brak dokładnej rewizji/snapshotu i mapowania wykorzystanych SWHIDs do plików oraz ich licencji |
| InfiMM-WebMath | Publikacja, sekcja 6, wymienia InfiMM-WebMath | Publikacja opisuje filtrację i warianty pochodne; nie należy przenosić licencji innego wariantu bez potwierdzenia | Brak dokładnej wersji, konfiguracji, proporcji i snapshotu |
| FineMath | Publikacja, sekcja 6, wymienia FineMath | Karta FineMath deklaruje ODC-By 1.0 i Common Crawl Terms of Use | Brak potwierdzenia konkretnego wariantu FineMath (np. 3+ / 4+), rewizji i snapshotu użytego w 360M |
| Cosmopedia | Publikacja, sekcja 6, wymienia Cosmopedia | Karta Cosmopedia v0.1 deklaruje Apache-2.0; karta opisuje także rozróżnienie wersji i pochodzenie danych seed | Brak potwierdzenia wersji (v0.1/v0.2), rewizji i splitów użytych w 360M |

**The Stack / Stack-Edu — szczególne zastrzeżenie:** BigCode opisuje The Stack jako zbiór kodu z repozytoriów o różnych licencjach. Użycie każdego pliku musi respektować jego oryginalne warunki, w tym klauzule atrybucji, a pochodzenie jest opisane na poziomie elementów. BigCode opisuje także proces usuwania danych i aktualizowania zbioru; karta Stack-Edu wskazuje SWHIDs i pobieranie treści z Software Heritage. Sam tag licencji datasetu nie jest więc wystarczającym dowodem zgodności każdego pliku. Dla finalnego treningu potrzebny byłby snapshot oraz provenance na poziomie SWHID/pliku.

### 3.2 Supervised fine-tuning (SFT)

Publikacja wskazuje filtrowany SmolTalk dla 360M/135M: autorzy usunęli złożone zadania instruction-following (np. function calling) i trudniejsze przykłady MagPie-Ultra, dopasowując dane do możliwości mniejszych modeli.

Karta `HuggingFaceTB/smol-smoltalk` potwierdza wprost:
- jest to podzbiór SmolTalk dla modeli poniżej 1B parametrów;
- użyto go do budowy SmolLM2-360M-Instruct i 135M-Instruct;
- był używany w SFT;
- karta deklaruje Apache-2.0.

To jest mocniejszy, bezpośredni dowód niż ogólne wskazanie pełnego `HuggingFaceTB/smoltalk`. Karta `smol-smoltalk` nie zastępuje jednak przypięcia rewizji danych faktycznie wykorzystanej w treningu ani pełnej listy źródeł składowych, z których powstał SmolTalk. W tym audycie nie potwierdzono dokładnego immutable revision użytego podczas treningu.

### 3.3 Direct Preference Optimization (DPO)

Publikacja wskazuje UltraFeedback jako dane do DPO dla modeli 360M/135M. Karta `HuggingFaceH4/ultrafeedback_binarized` deklaruje MIT i opisuje binarizację odpowiedzi (chosen/rejected) dla preference modelling/DPO. To jest użyteczny dowód na publiczny wariant datasetu, ale nie dowodzi, że dokładnie ta konkretna rewizja `HuggingFaceH4/ultrafeedback_binarized` była wejściem treningowym SmolLM2-360M.

Dodatkowe ryzyko provenance: UltraFeedback zawiera odpowiedzi wygenerowane przez różne modele, w tym modele proprietary, a oceny były przypisywane przez GPT-4. Licencja/tag przetworzonego zbioru nie rozstrzyga automatycznie praw do wszystkich upstream promptów i odpowiedzi. Brak dokładnego snapshotu oraz szczegółowego lineage wejścia DPO utrzymuje ten komponent w stanie niezamkniętym.

## 4. Propozycja `field_provenance`

Poniższa tabela jest propozycją pól do rejestru/raportu provenance. Nie zmienia schematu manifestu ani statusu modelu.

| Pole | Wartość / ustalenie | Źródło | Confidence | Status |
|---|---|---|---|---|
| `model_id` | `2` | `manifests/huggingface-models.json` | high | observed |
| `artifact_revision` | `25eef036a8d245a5a188b1b45d4b240a39bc9f24` | pinned GGUF repository revision in current manifest | high | pinned |
| `artifact_sha256` | `75c4346ef9e855ed630f80078a2430cf63aaca599e340360998a313070fcdc47` | current manifest / LFS metadata audit | high | observed |
| `base_model` | `HuggingFaceTB/SmolLM2-360M-Instruct` | pinned GGUF README and official model card | high | identified |
| `pretraining.datasets` | DCLM filtered by FineWeb-Edu classifier; Stack-Edu; InfiMM-WebMath; FineMath; Cosmopedia | SmolLM2 paper §6 | high | source-listed |
| `pretraining.dataset_revisions` | not established | no exact immutable revisions in cited training description | high | missing |
| `sft.dataset` | filtered `HuggingFaceTB/smol-smoltalk` subset | paper §6 and dataset card | high | source-listed |
| `sft.dataset_revision` | not established | public card does not identify training-time snapshot | high | missing |
| `dpo.dataset` | UltraFeedback | paper §6 | high | source-listed |
| `dpo.dataset_revision` | not established | exact training-time dataset snapshot/configuration not identified | high | missing |
| `argilla.synth_apigen` | not confirmed as direct 360M training input | generic model-card wording is not enough to map this dataset to 360M recipe | high | unverified / do-not-assert |
| `license.dataset_components` | multiple declarations and upstream obligations; The Stack v2 is per-file | dataset cards and BigCode governance docs | high | partial |
| `human_decision` | keep `review_required`; no admission | project policy / owner decision required | high | pending |

Recommended future shape (illustrative only):

```json
{
  "field_provenance": {
    "pretraining.datasets": {
      "value": ["DCLM filtered by FineWeb-Edu classifier", "Stack-Edu", "InfiMM-WebMath", "FineMath", "Cosmopedia"],
      "source": "https://arxiv.org/abs/2502.02737",
      "source_section": "6 SmolLM2 135M and 360M",
      "confidence": "high",
      "verification": "source_listed",
      "revision": null
    },
    "sft.dataset": {
      "value": "HuggingFaceTB/smol-smoltalk (filtered subset)",
      "source": "https://huggingface.co/datasets/HuggingFaceTB/smol-smoltalk",
      "confidence": "high",
      "verification": "source_listed",
      "revision": null
    },
    "dpo.dataset": {
      "value": "UltraFeedback",
      "source": "https://arxiv.org/abs/2502.02737",
      "source_section": "6 SmolLM2 135M and 360M",
      "confidence": "high",
      "verification": "source_listed",
      "revision": null
    },
    "human_decision.admission": {
      "value": "not_admitted",
      "source": "owner decision required",
      "confidence": "high",
      "verification": "pending",
      "revision": null
    }
  }
}
```

Interpretacja: `source_listed` oznacza, że źródło wymienia dataset w opisie procesu; nie oznacza, że dokładny snapshot, wszystkie pliki lub prawa do każdego elementu zostały zweryfikowane. `high` confidence dotyczy poprawności odczytu publicznego twierdzenia, a nie kompletności legal clearance.

## 5. Status i wymagane dalsze dowody

| Obszar | Status | Co zamknęłoby lukę |
|---|---|---|
| GGUF artifact identity/hash | pinned / observed | ponowna niezależna kontrola metadanych tylko jeśli rewizja artefaktu ulegnie zmianie |
| Base model declared license | Apache-2.0 declared | zachować przypiętą kartę i tekst licencji w evidence bundle |
| Pretraining data lineage | partial | dokładne snapshoty, konfiguracje, proporcje, filtry i wersje każdego źródła |
| The Stack / Stack-Edu | unresolved | snapshot + SWHID/file-level source license and attribution mapping + removal/update compliance evidence |
| SFT | source-listed | exact Smol-SmolTalk revision, split/configuration and source dataset lineage |
| DPO | source-listed | exact UltraFeedback revision/configuration and upstream provenance of prompts/completions |
| Overall license review | `review_required` | niezależny przegląd wszystkich powyższych komponentów oraz decyzja właściciela |

**Nie należy** ustawiać `license_verified=1`, `technical_readiness=ready`, dodawać admission scope ani modyfikować allowlisty na podstawie tego raportu.

## 6. Źródła

1. SmolLM2 paper, w szczególności §6 (recipe 360M/135M), §5.2–5.3 (SFT/DPO opis rodziny), §3.3.2 (FineMath): https://arxiv.org/abs/2502.02737
2. Oficjalna karta SmolLM2-360M-Instruct: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
3. Smol-SmolTalk — karta i deklaracja Apache-2.0: https://huggingface.co/datasets/HuggingFaceTB/smol-smoltalk
4. DCLM-Edu — karta deklarująca CC-BY-4.0 i użycie w SmolLM2-135M/360M: https://huggingface.co/datasets/HuggingFaceTB/dclm-edu
5. Stack-Edu: https://huggingface.co/datasets/HuggingFaceTB/stack-edu
6. The Stack / BigCode data governance and per-file license obligations: https://www.bigcode-project.org/docs/about/the-stack/
7. FineMath — karta i warunki ODC-By 1.0 / Common Crawl: https://huggingface.co/datasets/HuggingFaceTB/finemath
8. FineWeb-Edu — ODC-By 1.0 and Common Crawl Terms of Use: https://huggingface.co/datasets/HuggingFaceFW/fineweb-edu
9. Cosmopedia — declared Apache-2.0; version distinctions in dataset card: https://huggingface.co/datasets/HuggingFaceTB/cosmopedia
10. UltraFeedback Binarized — declared MIT; source and processing description: https://huggingface.co/datasets/HuggingFaceH4/ultrafeedback_binarized
11. Current project license audit: `docs/model-audit/license-review-2026-11-14.md`
12. Current model registry: `manifests/huggingface-models.json`

## 7. Bezpieczeństwo i zakres zmian

- Metadata/documentation-only. Nie pobierano wag ani datasetów i nie uruchamiano inferencji.
- Nie zmieniano manifestu modeli, allowlisty, statusu admission ani żadnych uprawnień.
- Nie zmieniano produkcyjnej bazy, rosteru, wyników, stanu turniejów ani usług.
- Nie dotykano `/home/debian/ai-agent/models`.
- Nie użyto płatnych usług.
- Ten raport nie jest zgodą na dalsze użycie modelu.


## 8. Uzupełniająca weryfikacja źródeł — 2026-11-16

Poniższe ustalenia rozszerzają pierwotny przegląd. Ważne: rewizja aktualnej karty/repozytorium nie jest automatycznie rewizją używaną podczas treningu modelu. Status `revision_observed` oznacza tylko publicznie widoczny commit źródła.

### 8.1 Rewizje kart i repozytoriów

| Źródło | Publicznie widoczna rewizja | Co potwierdza | Czego nie potwierdza |
|---|---|---|---|
| `HuggingFaceTB/smol-smoltalk` | karta `f73fe85`; upload danych `f80219b` | opis SFT i powiązanie z 360M-Instruct | snapshot użyty w finalnym treningu |
| `HuggingFaceTB/dclm-edu` | karta `dbad8ad`; dane `c09e73a` | deklaracja CC-BY-4.0, użycie do SmolLM2-135M/360M | dokładny snapshot/proporcja i filtr treningowy |
| `HuggingFaceTB/stack-edu` | README `eeec5ca`; dane są rozdzielone na katalogi językowe z własną historią | 125B-tokenowy korpus identyfikatorów SWHID pochodzący z The Stack v2 | dokładne pliki/SWHID i rewizja użyta w treningu |
| `HuggingFaceTB/finemath` | karta `e92b25a`; dane `finemath-3plus` `a7dd0df` | konfiguracje FineMath-3+ i 4+, ODC-By 1.0 oraz Common Crawl terms | wybór wariantu w treningu 360M |
| `HuggingFaceTB/cosmopedia` | karta `0ae6ec6`; katalog danych `70d58a8` | publiczna wersja v0.1, Apache-2.0 | wersja/splity użyte w treningu; v0.2 jest osobno w smollm-corpus |
| `HuggingFaceH4/ultrafeedback_binarized` | karta `3949bf5`; dane `2a3fb93` | MIT tag, wyjaśnienie chosen/rejected i użycia DPO | użyty snapshot i pełne upstream rights dla wszystkich odpowiedzi |
| `HuggingFaceFW/fineweb-edu` | wersje karty wymieniają v1.0–v1.4 i snapshoty Common Crawl | źródło klasyfikatora edukacyjności i wersjonowanie crawl | dokładna wersja klasyfikatora/snapshotów wykorzystana przy DCLM dla 360M |
| `HuggingFaceFW/fineweb-edu-classifier` | README `284663c` | publiczna historia modelu klasyfikującego | rewizja modelu klasyfikatora użyta do przygotowania danych 360M |
| `HuggingFaceTB/SmolLM2-360M-Instruct` | karta z rewizją bazową; plik LICENSE dodany w `cbcad7f` | deklaracja Apache-2.0 i istnienie pliku licencji | pełne dane treningowe |
| `QuantFactory/SmolLM2-360M-Instruct-GGUF` | plik Q4_K_M uploadowany w commicie `25eef03` | pochodzenie konkretnego pliku z repozytorium GGUF; zgodność prefiksu z rewizją w manifeście | niezależne potwierdzenie SHA-256 w tej aktualizacji |

### 8.2 Dodatkowe korekty i zastrzeżenia

**DCLM-Edu:** oficjalna karta opisuje zbiór jako filtrowaną wersję DCLM z użyciem FineWeb-Edu educational classifier. Podaje, że zbiór był użyty do SmolLM2-135M i 360M, deklaruje CC-BY-4.0 oraz opisuje progi punktacji. Nie ustala jednak kompletnej konfiguracji ani dokładnego snapshotu użytego w finalnym treningu. Dodatkowa karta/historia zawiera też ablation 360M na 200B tokenów z DCLM, FineWeb-Edu i DCLM-Edu; nie należy mylić tej ablation z pełnym 4T-tokenowym treningiem.

**FineMath:** karta rozróżnia FineMath-3+ (34B tokenów) i FineMath-4+ (9.6B tokenów, podzbiór 3+). Wzmianka o FineMath w recipe 360M nie wskazuje jednoznacznie, który wariant był użyty. Nie wybierać wariantu w rejestrze bez dowodu.

**InfiMM-WebMath:** identyfikacja źródła została doprecyzowana w sekcji 10.2: paper wskazuje InfiMM-WebMath-40B. Oryginalny zbiór deklaruje ODC-By; dokładny training-time snapshot, tekstowa ekstrakcja i ewentualny preprocessing użyty w 360M pozostają nieustalone. Nie utożsamiać oryginalnego InfiMM-WebMath-40B z pochodnymi `Infi-WebMath-3+` / `Infi-WebMath-4+` w FineMath.

**Cosmopedia:** publiczne repozytorium `HuggingFaceTB/cosmopedia` to v0.1 z Apache-2.0. Karta wskazuje, że v0.2 znajduje się w oddzielnym `HuggingFaceTB/smollm-corpus`. Nie ma dowodu, którą wersję i które splity wybrano do 360M.

**UltraFeedback:** karta opisuje 64k promptów, po cztery odpowiedzi wygenerowane przez różne modele (otwarte i proprietary), ocenione przez GPT-4; binarizacja tworzy chosen/rejected. MIT tag repozytorium nie jest samodzielnym dowodem praw do każdego promptu i completion. Dokładny snapshot UltraFeedback użyty w DPO SmolLM2 pozostaje nieustalony.

**FineWeb-Edu:** karta opisuje wersjonowanie v1.0–v1.4 i konkretne snapshoty Common Crawl. W małym model recipe źródło jest wskazane jako classifier do filtrowania DCLM. Nie dopisywać FineWeb-Edu jako bezpośredniego korpusu finalnego treningu 360M bez dodatkowego dowodu.

**The Stack v2 / Stack-Edu:** BigCode opisuje usuwanie repozytoriów po opt-out i wymóg korzystania z najnowszej dozwolonej wersji. Stack-Edu zawiera SWHID, a nie kod. Software Heritage ma własną politykę usuwania originów/snapshotów; nie jest to dowód automatycznego, natychmiastowego usunięcia każdego pojedynczego obiektu z każdego pochodnego datasetu. Brak manifestu SWHID użytego w treningu uniemożliwia sprawdzenie, czy konkretny zestaw odpowiadał właściwej wersji The Stack v2 i czy późniejsze opt-outy zostały obsłużone.

### 8.3 Stan zamknięcia braków

| Obszar | Wynik po partiach kontrolnych |
|---|---|
| Identyfikacja publicznych kart i deklarowanych licencji | sprawdzona |
| Publiczne rewizje części kart i danych | częściowo ustalone; nie są dowodem rewizji treningowej |
| Pretraining recipe dla 360M | potwierdzone na poziomie publikacji / kart |
| Dokładne snapshoty pretraining datasets | nieustalone |
| Wariant FineMath | nieustalony |
| Wariant Cosmopedia | nieustalony |
| Identyfikacja źródła InfiMM-WebMath | InfiMM-WebMath-40B identified; exact training-time snapshot/text extraction unresolved |
| SFT: Smol-SmolTalk | źródło i publiczny commit ustalone; training-time revision i upstream lineage nieustalone |
| DPO: UltraFeedback | publiczna karta/commity ustalone; training-time revision i upstream rights nieustalone |
| Stack-Edu file-level licensing and SWHID list | nieustalone |
| Dokładne proporcje, filtry i token mix | nieustalone |
| Ogólny status licencji | `review_required` |

### 8.4 Źródła dodatkowej weryfikacji

- Smol-SmolTalk card/history: https://huggingface.co/datasets/HuggingFaceTB/smol-smoltalk/commits/main
- DCLM-Edu card/history: https://huggingface.co/datasets/HuggingFaceTB/dclm-edu/commits/main
- Stack-Edu card/history: https://huggingface.co/datasets/HuggingFaceTB/stack-edu/commits/main
- FineMath card/history: https://huggingface.co/datasets/HuggingFaceTB/finemath/commits/main
- Cosmopedia card/history: https://huggingface.co/datasets/HuggingFaceTB/cosmopedia/commits/main
- UltraFeedback card/history: https://huggingface.co/datasets/HuggingFaceH4/ultrafeedback_binarized/commits/main
- FineWeb-Edu card: https://huggingface.co/datasets/HuggingFaceFW/fineweb-edu
- FineWeb-Edu classifier history: https://huggingface.co/HuggingFaceFW/fineweb-edu-classifier/commits/main
- SmolLM2 base model license/history: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- GGUF file history: https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF/commits/79a458dacceaf3605eb8924d6d85bb6fa391a0ad
- BigCode The Stack v2 terms: https://www.bigcode-project.org/docs/about/the-stack/
- The Stack v2 dataset versions: https://huggingface.co/datasets/bigcode/the-stack-v2
- Software Heritage content policy: https://www.softwareheritage.org/legal/content-policy/
- Software Heritage removal algorithm: https://docs.softwareheritage.org/devel/swh-alter/removal-algorithm.html

## 9. Końcowy wniosek po dodatkowej weryfikacji

Kontrola publicznych źródeł została rozszerzona do kart, historii commitów, deklaracji licencji i polityk źródeł upstream. Nie udało się jednak odtworzyć dokładnego data bill of materials użytego w treningu 360M. W szczególności nie znamy wszystkich trening-time dataset revisions, proporcji i konfiguracji, wariantu FineMath/Cosmopedia, dokładnego training-time snapshotu/tekstowej ekstrakcji InfiMM-WebMath-40B ani pełnego manifestu SWHID dla Stack-Edu.

W konsekwencji ten audyt **nie zamyka przeglądu licencji**. Zachować `review_required`; nie zmieniać manifestu, allowlisty, technicznej gotowości, zakresów admission ani stanu turniejów.


## 10. Weryfikacja konfiguracji treningowych i doprecyzowanie wniosków — 2026-11-16

### 10.1 Pretraining: oficjalny checkpoint 360M

Odczytano publiczny plik `HuggingFaceTB/SmolLM2-nanotron-ckpt/360M/final/config.yaml`:
- repozytorium checkpointów: `HuggingFaceTB/SmolLM2-nanotron-ckpt`;
- commit pliku widoczny na stronie HF: `1dfdc90` (verified);
- `dataset_folder: datasets/smollm2-corpus`;
- `dataset_weights: [1.0]`;
- tokenizer: `HuggingFaceTB/cosmo2-tokenizer`, bez przypiętej rewizji;
- długość sekwencji: 2048;
- `train_steps: 2600000`;
- `learning_rate: 0.003`, WSD/linear decay w końcowej części;
- folder checkpointu to `360M/final`, a parametry modelu (hidden size 960, 32 warstwy, 15 heads) są zgodne z małym wariantem.
- Uwaga: pole `run` w tym configu ma wartość `smollm2-1700M`, mimo ścieżki `360M/final` i małej konfiguracji modelu. To wygląda na niespójny opis run metadata; nie należy na tej podstawie zmieniać identyfikacji modelu. Warto zachować jako anomalię pochodzenia configu.

Konfiguracja potwierdza, że właściwy run wskazuje na jeden zagregowany katalog `smollm2-corpus`, nie na listę publicznych datasetów z osobnymi wagami. Nie zawiera manifestu komponentów, snapshotów, wewnętrznych proporcji ani hashy tokenizowanych shardów. Nie można zatem odtworzyć dokładnego BOM pretrainingu z samego checkpoint config.

### 10.2 Identyfikacja InfiMM-WebMath — korekta poprzedniego raportu

Publikacja SmolLM2 w sekcji 3.3.1 cytuje pracę InfiMM-WebMath-40B, a sekcja 6 wymienia InfiMM-WebMath w pojedynczym etapie treningu 135M/360M. Oryginalna praca InfiMM opisuje 40B tekstowych tokenów, 24 mln stron i 85 mln URL-i obrazów; oficjalna karta wskazuje repozytorium `Infi-MM/InfiMM-WebMath-40B` i deklarację ODC-By.

Wniosek poprawiony:
- rodzina/źródło InfiMM-WebMath jest zidentyfikowana jako InfiMM-WebMath-40B;
- trening małego modelu jest tekstowym LM, więc nie należy zakładać użycia obrazów tylko dlatego, że oryginalny dataset jest multimodalny;
- nie potwierdzono, czy wykorzystano surowy komponent tekstowy 40B, czy konkretny przefiltrowany/wyekstrahowany snapshot;
- nie wolno utożsamiać oryginalnego InfiMM-WebMath-40B z pochodnym `Infi-WebMath-3+` / `Infi-WebMath-4+` w repozytorium FineMath. Paper wymienia InfiMM-WebMath obok FineMath, a nie zamiast niego.

### 10.3 FineMath i Cosmopedia — status wariantów

- FineMath: sekcja 6 wymienia tylko nazwę FineMath; nie wskazuje 3+ ani 4+. Repozytorium treningowe zawiera tokenizację obu wariantów i eksperymenty/ablacje 3+/4+, lecz nie znaleziono configu właściwego runu 360M, który rozstrzygałby wybór. Status: `variant_unverified`.
- Cosmopedia: sekcja 6 wymienia tylko nazwę Cosmopedia. Publiczna karta `HuggingFaceTB/cosmopedia` opisuje v0.1, a `HuggingFaceTB/smollm-corpus` zawiera Cosmopedia v2. Config 360M wskazuje tylko zagregowany `smollm2-corpus`; nie ma mapowania do wersji ani splitów. Status: `variant_unverified`.
- Nie przenosić `Cosmopedia v2` z wieloetapowego recipe 1.7B do 360M. Dla 360M paper potwierdza single-stage mix, ale nie określa wersji Cosmopedia.

### 10.4 SFT — oficjalna konfiguracja dla 360M/135M

Repozytorium Hugging Face Alignment Handbook zawiera `recipes/smollm2/sft/config_smol.yaml`:
- model: `HuggingFaceTB/SmolLM2-360M` (ta sama konfiguracja jest używana również dla 135M);
- `dataset_name: HuggingFaceTB/smol-smoltalk`;
- split train/test;
- `num_train_epochs: 2`;
- `max_seq_length: 8192`;
- `dataset_num_proc: 24`;
- `model_revision: main`;
- brak `dataset_revision` — rewizja datasetu nie jest immutable-pinned.

To mocniej potwierdza dokładną nazwę datasetu i ustawienia publicznego recipe SFT. Nie dowodzi jednak, że aktualna gałąź `main` jest dokładnie snapshotem z historycznego uruchomienia. Nie znaleziono publicznego training-run manifestu z immutable dataset SHA.

### 10.5 DPO — oficjalna konfiguracja dla 360M/135M

Repozytorium Alignment Handbook zawiera `recipes/smollm2/dpo/config_smol.yaml`:
- model wejściowy: `loubnabnl/smollm2-360M-sft`;
- dataset: `HuggingFaceH4/ultrafeedback_binarized`;
- config: `default`;
- train split: `train_prefs`;
- test split: `test_prefs`;
- kolumny: `chosen`, `rejected`;
- `num_train_epochs: 2`;
- brak `dataset_revision` — nie ma immutable pin dla snapshotu UltraFeedback.

Wynik: recipe DPO potwierdza dokładny identyfikator datasetu i splitów, ale nie dokładny commit danych użyty w historycznym runie. Deklaracja MIT na karcie datasetu nie jest równoznaczna z pełnym upstream rights clearance.

### 10.6 Wynik zamknięcia kolejnych luk

| Pytanie | Ustalenie | Status |
|---|---|---|
| Jaki dataset path wskazuje checkpoint 360M? | `datasets/smollm2-corpus` (waga 1.0) | confirmed |
| Czy checkpoint config rozbija pretraining na publiczne datasety? | nie | confirmed_missing |
| Czy źródło InfiMM jest zidentyfikowane? | InfiMM-WebMath-40B | identified |
| Czy znany jest exact InfiMM training snapshot/text extraction? | nie | unresolved |
| Czy znany jest FineMath 3+ vs 4+? | nie | unresolved |
| Czy znana jest Cosmopedia v0.1 vs v0.2? | nie | unresolved |
| Czy znamy SFT dataset ID i config? | smol-smoltalk; config_smol.yaml | confirmed |
| Czy znamy immutable SFT dataset revision? | nie; config wskazuje model main, nie dataset commit | unresolved |
| Czy znamy DPO dataset ID/config/splits? | ultrafeedback_binarized/default/train_prefs/test_prefs | confirmed |
| Czy znamy immutable DPO dataset revision? | nie | unresolved |
| Czy znamy pełne source-lineage komponentów SFT/DPO? | nie | unresolved |

### 10.7 Źródła

- SmolLM2 paper §3.3.1, §3.3.2 i §6: https://arxiv.org/abs/2502.02737
- Official 360M checkpoint config: https://huggingface.co/HuggingFaceTB/SmolLM2-nanotron-ckpt/blob/main/360M/final/config.yaml
- Checkpoint repository: https://huggingface.co/HuggingFaceTB/SmolLM2-nanotron-ckpt
- InfiMM-WebMath-40B paper: https://arxiv.org/abs/2409.12568
- InfiMM-WebMath-40B dataset: https://huggingface.co/datasets/Infi-MM/InfiMM-WebMath-40B
- FineMath card/configs: https://huggingface.co/datasets/HuggingFaceTB/finemath
- Cosmopedia v0.1: https://huggingface.co/datasets/HuggingFaceTB/cosmopedia
- SmolLM-Corpus / Cosmopedia v2: https://huggingface.co/datasets/HuggingFaceTB/smollm-corpus
- Alignment Handbook SFT config: https://github.com/huggingface/alignment-handbook/blob/main/recipes/smollm2/sft/config_smol.yaml
- Alignment Handbook DPO config: https://github.com/huggingface/alignment-handbook/blob/main/recipes/smollm2/dpo/config_smol.yaml
- Alignment Handbook recipe README: https://github.com/huggingface/alignment-handbook/blob/main/recipes/smollm2/README.md

## 11. Zaktualizowany wniosek

Korekta względem wcześniejszych wersji:
- InfiMM-WebMath nie jest już oznaczone jako niezidentyfikowane: paper wskazuje InfiMM-WebMath-40B; nierozstrzygnięte pozostają exact snapshot i tekstowa postać użyta w treningu.
- FineMath i Cosmopedia są potwierdzone na poziomie nazwy, ale warianty nie są potwierdzone.
- SFT i DPO mają publiczne recipe z dokładnymi identyfikatorami datasetów, splitami i parametrami, ale bez immutable dataset revisions.
- Pretraining config wskazuje zagregowany `smollm2-corpus`, więc nie daje per-component dataset bill of materials.

Status modelu pozostaje `review_required`. Nie zmieniać manifestu, allowlisty, admission, technical readiness, produkcji ani turniejów na podstawie tej dokumentacji.


## 12. Dalsza kontrola: Stack-Edu lineage oraz historyczne recipe SFT/DPO — 2026-11-16

### 12.1 Stack-Edu — zakres pól i ograniczenie identyfikowalności

Publiczna karta `HuggingFaceTB/stack-edu` opisuje dataset jako 125B-tokenowy zbiór edukacyjnego kodu z The Stack v2. Repozytorium Stack-Edu przechowuje identyfikatory SWHID, a nie treść kodu; zawiera też pola:
- `blob_id` (SWHID pliku),
- `language`,
- `repo_name`,
- `path`,
- `detected_licenses`,
- `license_type`,
- `score` / `int_score`,
- `length_bytes` i `src_encoding`.

Karta wyjaśnia, że treść plików pobiera się z Software Heritage S3. Licensing section dodana w publicznej historii repozytorium 20 marca 2025 r. stwierdza, że zbiór zawiera kod z repozytoriów o różnych licencjach, a użycie całości lub części musi przestrzegać oryginalnych licencji, w tym atrybucji; provenance ma to ułatwiać.

Istotne porównanie schematów:
- Stack-Edu publicznie prezentuje SWHID/blob i część metadanych licencyjnych, ale nie pokazuje w swoim schemacie pól `snapshot_id`, `revision_id`, `branch_name`, `visit_date` i `revision_date`.
- The Stack v2 ma te pola w swoim pełniejszym schemacie.
- Zatem z pojedynczego rekordu Stack-Edu nie da się bez dodatkowego join/manifestu odtworzyć jednoznacznie snapshotu i rewizji repozytorium źródłowego. `blob_id` identyfikuje treść, nie pełny kontekst repozytorium i moment pobrania.
- `detected_licenses` jest metadanymi automatycznej detekcji (ScanCode w The Stack v2), a nie gwarancją kompletności lub poprawności prawnej. `license_type` jest klasyfikacją uproszczoną (np. permissive/no_license), a nie decyzją prawną dla każdego pliku.

### 12.2 Chronologia publikacji Stack-Edu — nie utożsamiać z trening-time snapshotem

Publiczna historia repozytorium pokazuje:
- 19 marca 2025: commit `b5648b91956d46c72dcfd85209b1714c48fa8312` dodał główną kartę Stack-Edu;
- 20 marca 2025: commit `fdb670bb704c445d32a9d1a3684a15a758b9f913` dodał sekcję Licensing Information;
- publiczny opis wskazuje na 125B-tokenowy dataset edukacyjnego kodu.

Paper SmolLM2 został opublikowany w lutym 2025 r. Ta chronologia oznacza, że obecnej publicznej karty i jej rewizji nie można traktować jako dowodu snapshotu użytego w treningu. Dataset mógł istnieć wcześniej wewnętrznie lub jako StarCoder2Data, ale dostępne publiczne commity same w sobie nie wykazują, jaki dokładnie zbiór i rewizję wykorzystano w 360M.

Wniosek: wcześniejsze sformułowanie, że Stack-Edu publicznie ma historię commitów, jest poprawne, ale nie wolno sugerować, że wskazany commit jest training-time revision. Status pozostaje `training_snapshot_unverified`.

### 12.3 Historyczne SFT recipe — commit pierwotnego pipeline

Git commit `ae3f44fc7d8003d706752ca06f689574dffa3b76` w `huggingface/alignment-handbook` z 21 listopada 2024 r. dodał pipeline SmolLM2. Pliki w tym konkretnym commicie pokazują:
- SFT config `recipes/smollm2/sft/config_smol.yaml`;
- model `HuggingFaceTB/SmolLM2-360M` (komentarz mówi, że ten sam skrypt służy też dla 135M);
- tokenizer `HuggingFaceTB/SmolLM2-360M-Instruct`;
- `dataset_mixer: HuggingFaceTB/smol-smoltalk: 1.0`;
- splity `train` i `test`;
- 2 epoki, max sequence length 8192;
- brak `dataset_revision` lub innego immutable pin dla datasetu.

README recipe w tym samym repozytorium mówi wprost, że dla 135M i 360M używa się Smol-SmolTalk do SFT. Jest to silny dowód publicznego recipe przypisanego do wariantów 135M/360M, ale nie dowód, że historyczny run użył dokładnie tego commita repozytorium ani konkretnego snapshotu datasetu.

### 12.4 Historyczne DPO recipe — commit pierwotnego pipeline

Ten sam commit `ae3f44fc7d8003d706752ca06f689574dffa3b76` zawiera:
- DPO config `recipes/smollm2/dpo/config_smol.yaml`;
- model wejściowy `loubnabnl/smollm2-360M-sft` (komentarz wskazuje także użycie dla 135M);
- `dataset_mixer: HuggingFaceH4/ultrafeedback_binarized: 1.0`;
- splity `train_prefs` i `test_prefs`;
- 2 epoki, beta 0.5, max length 1024 i max prompt length 512;
- brak `dataset_revision` lub immutable pin.

README wskazuje wprost, że dla 135M i 360M UltraFeedback jest używany do DPO. To potwierdza publiczne recipe i konfigurację źródłową, nie exact historical data snapshot. Aktualne recipe zmieniło składnię konfiguracji (dataset mixture), ale nadal nie zawiera dataset revision. Nie traktować aktualnej gałęzi `main` jako dowodu snapshotu z 2024 r.

### 12.5 Wniosek po kolejnej partii

| Luka | Nowy dowód | Status |
|---|---|---|
| Czy Stack-Edu zawiera identyfikatory SWHID? | tak, `blob_id` / SWHID | confirmed |
| Czy Stack-Edu ma per-row licencyjne pola? | tak, `detected_licenses`, `license_type` | confirmed |
| Czy Stack-Edu ma pełny snapshot/revision lineage w swoim schemacie? | nie; brak `snapshot_id` / `revision_id` w publicznym schemacie | confirmed_missing |
| Czy publiczna karta Stack-Edu jest training-time snapshotem 360M? | nie można tego wykazać; karta/licensing info opublikowane w marcu 2025 | unresolved |
| Czy historyczne SFT recipe wskazuje dataset i split? | smol-smoltalk, train/test, mixer 1.0 | confirmed_recipe |
| Czy historyczne DPO recipe wskazuje dataset i split? | ultrafeedback_binarized, train_prefs/test_prefs, mixer 1.0 | confirmed_recipe |
| Czy historyczne SFT/DPO recipe pinują dataset commit? | nie | confirmed_missing |
| Czy te recipe dowodzą dokładnego run-time dataset snapshotu? | nie | unresolved |

### 12.6 Źródła

- Stack-Edu card/schema: https://huggingface.co/datasets/HuggingFaceTB/stack-edu
- Stack-Edu initial public card commit (19 Mar 2025): https://huggingface.co/datasets/HuggingFaceTB/stack-edu/commit/b5648b91956d46c72dcfd85209b1714c48fa8312
- Stack-Edu licensing addition (20 Mar 2025): https://huggingface.co/datasets/HuggingFaceTB/stack-edu/commit/fdb670bb704c445d32a9d1a3684a15a758b9f913
- The Stack v2 full schema: https://huggingface.co/datasets/bigcode/the-stack-v2
- Dataset Viewer API documentation: https://huggingface.co/docs/dataset-viewer/en/quick_start
- Alignment Handbook initial SmolLM2 pipeline commit (21 Nov 2024): https://github.com/huggingface/alignment-handbook/commit/ae3f44fc7d8003d706752ca06f689574dffa3b76
- Historical SFT config at that commit: https://raw.githubusercontent.com/huggingface/alignment-handbook/ae3f44fc7d8003d706752ca06f689574dffa3b76/recipes/smollm2/sft/config_smol.yaml
- Historical DPO config at that commit: https://raw.githubusercontent.com/huggingface/alignment-handbook/ae3f44fc7d8003d706752ca06f689574dffa3b76/recipes/smollm2/dpo/config_smol.yaml
- Alignment Handbook README at initial recipe commit: https://raw.githubusercontent.com/huggingface/alignment-handbook/ae3f44fc7d8003d706752ca06f689574dffa3b76/recipes/smollm2/README.md
- Current SFT config: https://github.com/huggingface/alignment-handbook/blob/main/recipes/smollm2/sft/config_smol.yaml
- Current DPO config: https://github.com/huggingface/alignment-handbook/blob/main/recipes/smollm2/dpo/config_smol.yaml

## 13. Updated fail-closed decision

The audit now distinguishes:
1. dataset identity and public recipe (confirmed);
2. public repository/card revisions (observed);
3. exact training-time immutable dataset revisions and full source-file lineage (not established).

The Stack-Edu schema supplies useful per-file SWHID and license-detection metadata, but does not on its own supply complete source snapshot lineage. The historical SFT/DPO recipe commit confirms the dataset names, mixture weights, and splits but has no dataset commit pins.

No new evidence justifies changing `review_required`. Do not change manifest, allowlist, license verification, technical readiness, admission scope, production, database, roster, or tournament state.


## 14. Public availability of the complete SmolLM2 pretraining corpus — upstream confirmation

### 14.1 Upstream issue #35 — direct project discussion

The upstream `huggingface/smollm` issue #35, “SmolLM2 Pretrain Dataset”, was opened on 18 December 2024. Its question explicitly notes that model cards described the pretraining dataset as forthcoming.

The maintainer response on 23 December 2024 states that FineWeb-Edu, DCLM and FineMath were already public, while the code dataset built on The Stack v2 was the exception; information about how the sources were mixed was promised in a technical report.

A later follow-up on 7 March 2025 asks whether the exact training samples were publicly available and notes that the configuration points to a single `smollm2-corpus` dataset folder which was not available as a whole. The thread does not provide a later response releasing the complete corpus. On 29 July 2025, a contributor only confirms that Stack-Edu is now available.

This is direct upstream evidence that:
- public availability of individual source datasets is not equivalent to publication of the assembled training corpus;
- the complete sample-level corpus and its reproducible manifest were not established by the public repository/configuration discussed in the thread;
- the later publication of Stack-Edu closes only the availability gap for that component, not the full corpus assembly/provenance gap.

### 14.2 Public model card and paper — reconciled scope

The official SmolLM2-360M-Instruct model card says the model was trained on 4T tokens using FineWeb-Edu, DCLM, The Stack, and additional curated/filtered datasets; it points to the SmolLM2 paper for further detail. The model card's broad summary is not itself a full bill of materials.

The paper's dedicated section for 135M/360M identifies the single-stage mixture more precisely:
- DCLM filtered with the FineWeb-Edu classifier; score 0 removed, scores 1 and 2 downsampled;
- Stack-Edu;
- InfiMM-WebMath;
- FineMath;
- Cosmopedia.

It states 4T tokens for 360M and 2T for 135M. The paper's 1.7B multi-stage percentages and stage-4 additions must not be projected onto 360M.

### 14.3 Related public 135M sample — explicitly not transferable to 360M

The public `EleutherAI/SmolLM2-135M-10B` dataset describes a 10B-token sample from the 135M pretraining data. It lists FineMath (34B), Stack-Edu (125B), InfiMM-WebMath (40B), Cosmopedia V2 (30B), FineWeb-Edu (710.4B sampled from 1.2T) and DCLM-Edu (1065.6B sampled from 3.8T), with selected DCLM-Edu/FineWeb-Edu portions sampled at 6:4.

This is evidence about a public sample derived from the 135M mixture, not a source manifest for the 360M run. It cannot be used to infer 360M proportions, source revisions, sample IDs, or token counts. The 360M paper independently confirms the source family but does not publish a complete 360M sample-level manifest in the materials examined.

### 14.4 Updated evidence matrix

| Question | Evidence | Status |
|---|---|---|
| 360M pretraining token budget | Official model card and paper: 4T | confirmed |
| 360M training strategy | Paper §6: single-stage | confirmed |
| 360M source dataset families | Paper §6: filtered DCLM, Stack-Edu, InfiMM-WebMath, FineMath, Cosmopedia | confirmed |
| Exact 360M source proportions | No complete 360M table or training manifest found | unresolved |
| Complete assembled `smollm2-corpus` publicly downloadable | Upstream issue #35 says config referenced a corpus unavailable as a whole; no full corpus release is evidenced in the thread | not_established |
| Exact sample IDs and per-source revisions for 360M | No immutable training BOM/sample manifest found | unresolved |
| Can 135M-10B sample proportions be used for 360M? | No; dataset explicitly samples 135M pretraining data | not_transferable |
| Does Stack-Edu publication alone close corpus gap? | No; it publishes one component, not the entire assembled corpus | confirmed_missing |

### 14.5 Sources

- Upstream project issue and maintainer/contributor replies: https://github.com/huggingface/smollm/issues/35
- Official SmolLM2-360M-Instruct model card, especially Model Summary: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- SmolLM2 paper, §6 “SmolLM2 135M and 360M”: https://arxiv.org/abs/2502.02737
- Public 135M-10B sample and its scope: https://huggingface.co/datasets/EleutherAI/SmolLM2-135M-10B
- Stack-Edu component published in 2025: https://huggingface.co/datasets/HuggingFaceTB/stack-edu
- Official 360M checkpoint config points to one local folder `datasets/smollm2-corpus`, with dataset weight 1.0, but contains no component-level bill of materials: https://huggingface.co/HuggingFaceTB/SmolLM2-nanotron-ckpt/blob/main/360M/final/config.yaml

## 15. Final status of the pretraining-corpus provenance gap

The new upstream issue evidence materially strengthens the conclusion: the public sources identify the 360M training recipe at the dataset-family level, but the exact assembled corpus, 360M source proportions, sample IDs, and immutable dataset revisions remain unverified. The separate 135M sample is not a substitute.

This is a documentation/provenance limitation, not proof that the training data was never released through any private or later channel. No such complete release was established in this audit.

The correct fail-closed result remains `review_required`. Do not infer legal clearance, source-level compliance, or reproducibility from the Apache-2.0 model-card tag alone. No manifest, allowlist, permission, technical-readiness, admission, production, DB, roster, or tournament changes.


## Execution isolation boundary

Any future runtime verification for this model must follow [`docs/model-execution-isolation-policy.md`](../model-execution-isolation-policy.md): execution and artifact storage are restricted to the already-existing disposable LXC `arena-test`. Host-side model execution is prohibited. If that container is unavailable, do not create a replacement or fall back to the host; leave the result `NOT_VERIFIED`/`SKIPPED_BLOCKED` with evidence.
