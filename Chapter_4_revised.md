# Chapter 4 — Maritime Knowledge Extraction

Maritime accident investigations describe the circumstances of an incident, the actions of the vessels involved, and the factors identified by investigators. These findings are potentially useful to a navigation agent, but the reports are lengthy narrative documents rather than records that can be searched directly against the current vessel state. This chapter describes how MAIB investigation reports were converted into structured lesson cards and how those cards were made available to the Captain agent through retrieval.

The method addresses the first research question: how can unstructured maritime accident reports be transformed into source-grounded, structured reasoning data for an autonomous navigation workflow? The chapter describes the data preparation, extraction, validation, filtering, and runtime retrieval procedures. Their experimental outcomes are reported in Chapter 8.

## 4.1 Source Corpus and Text Preparation

The source material consists of 107 Marine Accident Investigation Branch (MAIB) reports selected from collision- and grounding-related investigations. Reports were processed as PDF documents. Text was extracted separately from each page and repeated whitespace was normalised. Figures, photographs, and information unavailable as extracted text were not included.

The extracted text was divided into page-bounded chunks of at most 400 words, with a 60-word overlap between consecutive chunks. Fragments shorter than 80 words were not retained. In total, 1,622 pages contributed 2,373 indexed chunks. Each chunk carried the identity of its source report and its physical PDF page number. These references were retained so that supporting quotations could later be checked against the source text.

For lesson extraction, the chunks were regrouped by report. Each model request contained all indexed chunks from one report, together with their page references. This allowed findings and supporting explanations from different sections of the same investigation to be considered together.

## 4.2 Lesson-Card Representation

The unit of extracted knowledge is a *lesson card*, representing a distinct investigator finding and an operational implication derived from that finding. Cards are intended to describe circumstances relevant to navigation reasoning, rather than prescribe a manoeuvre for every similar encounter.

**Table 4.1 — Information contained in a lesson card**

| Component | Contents |
|---|---|
| Provenance | Source report, cited pages, and exact supporting quotations |
| Encounter | Encounter type, vessel role, encounter phase, and warning signs |
| Finding | Investigator finding and contributing causal mechanisms |
| Lesson | Operational implication and conditions limiting its applicability |

Encounter descriptors use controlled categories, including crossing, head-on, overtaking, and restricted-visibility situations. Where the investigation does not establish a particular classification, the schema permits an uncertain or unknown value. This avoids assigning a definite encounter role solely to complete a record.

Each card contains one to three quotations linked to their source pages. The quotations preserve the original evidence, while the investigator finding and transferable lesson express the model's interpretation of that evidence. The non-transfer conditions record circumstances that would limit the relevance of the finding to another encounter.

## 4.3 Lesson Extraction and Validation

### 4.3.1 Extraction

Lesson generation used `gpt-6.1-sol` through the OpenAI Batch API. One request was prepared for each report, containing its full indexed text. The model was instructed to identify distinct investigator findings that could inform navigation decisions, particularly findings concerning collision assessment, vessel behaviour, delayed or ineffective action, monitoring, and interpretation of available navigation information. Each finding was converted into a card conforming to a predefined JSON Schema. The model could return multiple cards from one report, or none where no relevant finding was identified.

The instruction required an exact page-identified quotation for every card and discouraged unsupported assumptions about COLREG responsibilities, encounter geometry, or specific course and speed changes. The schema enforced the required fields and controlled vocabulary. Extraction was performed with low reasoning effort and a maximum completion allowance of 12,000 tokens per report.

### 4.3.2 Source validation

Generated cards were checked against the indexed report text before inclusion in the corpus. The validator checked schema conformance, confirmed that cited pages belonged to the source report, and matched every quotation against text extracted from the claimed page after Unicode and whitespace normalisation. A card was rejected if any supporting quotation could not be verified. Deterministic card identifiers based on the source report and normalised finding were used to identify duplicate records. The final cited-page field was derived from the pages of successfully verified quotations.

These checks establish the traceability of the quoted evidence. They are distinct from evaluating whether a generated lesson is relevant to the navigation decisions supported by the system. That assessment was performed in a separate filtering stage.

## 4.4 Filtering for Navigation Relevance

The source-validated cards were passed through a second, report-grouped language-model assessment. The model received the cards and their supporting quotations and assigned three labels to each record:

- **Decision relevance:** `direct` for findings applicable to the type of navigation reasoning supported by the system, `adjacent` for related maritime issues, and `out_of_scope` for findings outside that task.
- **Evidence support:** `strong`, `partial`, or `weak`, according to how clearly the supplied quotations supported the finding and its interpretation.
- **Transferability:** `safe`, `review`, or `reject`, according to whether the proposed lesson remained conditional on the circumstances of the original investigation.

The resulting labels were converted into a disposition using a fixed rule. Cards labelled simultaneously `direct`, `strong`, and `safe` were retained, provided they were not marked as duplicates. Cards with intermediate assessments were assigned to review, while cards classified as out of scope, weakly supported, unsuitable for transfer, or duplicates were excluded.

This filtering stage selected records for the intended navigation interface. Its labels were model-generated assessments rather than independent judgments by maritime investigators. The number of records retained and their distribution are reported in Chapter 8.

## 4.5 Retrieval from the Navigation State

The purpose of retrieval is to select historical lessons relevant to the vessel's current situation and provide them as additional context to the Captain agent. In contrast to extraction, which operates on accident-report text, retrieval operates on a live navigation state and searches the lesson-card index globally.

### 4.5.1 Query construction

Retrieval is requested when at least one observed contact is closing, has a non-negative TCPA, and has a predicted CPA below the configured clearance threshold. The retrieval component then builds a natural-language query from the available state information. It describes up to three contacts, using relative bearing, heading, speed, closing behaviour, CPA, and TCPA, together with visibility and own-ship control state. Where the Captain is reviewing an OOW proposal, the proposed action can also be included in the query.

The query therefore describes the current encounter in terms that can be compared with the circumstances and warning signs recorded in the cards. It is not an exact-text search for an accident report title or a previously identified case.

### 4.5.2 Card ranking and selection

Each card is indexed as a single retrieval document containing its encounter descriptors, warning signs, investigator finding, transferable lesson, non-transfer conditions, and source quotations. Two complementary ranking methods are used.

BM25 ranks cards by lexical correspondence with the query. It uses `k₁ = 1.5`, which controls the effect of repeated terms, and `b = 0.75`, which controls document-length normalisation. Dense retrieval uses `BAAI/bge-small-en-v1.5` to represent the query and indexed cards as vectors and ranks them by cosine similarity. The dense component can therefore identify related descriptions even when the query and card use different wording.

The highest-ranked 50 candidates from each method are combined using Reciprocal Rank Fusion (RRF), with a fusion constant of 60. RRF combines their positions in the two rankings rather than adding lexical and vector-similarity scores directly. The implementation also applies fixed weighting to favour text containing investigative analysis over material dominated by references to regulations.

In addition to the text-based ranking, the navigation state supplies encounter-type and encounter-phase preferences derived from the observed geometry and TCPA. Cards matching these preferences are ranked ahead of cards without a match; the fused retrieval score resolves the ordering within these groups. Finally, the selection retains at most one card from each source report. The configured runtime output is limited to the two highest-ranked cards remaining after this selection.

### 4.5.3 Use by the Captain agent

The retrieved cards are included in the Captain's input with their source references, investigator findings, lessons, and applicability conditions. The Captain may use this historical context when assessing the situation or a proposed manoeuvre. The cards do not determine the vessel's action: the Captain's decision is made using the current navigation state, the available action interface, and the regulatory information supplied by the separate COLREG component described in Chapter 5.

The navigation experiments in Chapter 8 evaluate the contribution of MAIB retrieval in the tested agent configuration. The extraction and retrieval procedures described here define how that historical information is prepared and supplied; their effect on navigation performance is treated as an experimental question rather than assumed from the existence of the cards.