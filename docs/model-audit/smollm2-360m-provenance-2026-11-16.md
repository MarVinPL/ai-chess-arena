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

**InfiMM-WebMath:** odnalezione repozytorium `Infi-MM/InfiMM-WebMath-40B` ma własną deklarację ODC-By, ale nie potwierdzono, że jest to dokładny dataset/wariant wskazany w SmolLM2 recipe. FineMath zawiera także `InfiWebMath-3+`; nie utożsamiać nazw `InfiMM-WebMath` i `InfiWebMath` bez dowodu. Wartość licencji i rewizji dla źródła użytego w 360M pozostaje nieustalona.

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
| Dokładny InfiMM-WebMath | nieustalony; nie utożsamiać z InfiMM-WebMath-40B ani InfiWebMath |
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

Kontrola publicznych źródeł została rozszerzona do kart, historii commitów, deklaracji licencji i polityk źródeł upstream. Nie udało się jednak odtworzyć dokładnego data bill of materials użytego w treningu 360M. W szczególności nie znamy wszystkich trening-time dataset revisions, proporcji i konfiguracji, wariantu FineMath/Cosmopedia, właściwej identyfikacji InfiMM-WebMath ani pełnego manifestu SWHID dla Stack-Edu.

W konsekwencji ten audyt **nie zamyka przeglądu licencji**. Zachować `review_required`; nie zmieniać manifestu, allowlisty, technicznej gotowości, zakresów admission ani stanu turniejów.
