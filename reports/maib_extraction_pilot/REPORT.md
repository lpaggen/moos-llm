Chapter 4 — Extracting and Evaluating Lessons from Maritime Accident Reports
4.1 Research objective
This chapter describes the extraction of navigation-related lessons from Marine Accident Investigation Branch (MAIB) reports and the experiments used to evaluate the resulting data. The objective was to convert narrative investigation findings into records that a navigation agent could retrieve when assessing a vessel encounter. Each record identifies the reported circumstances, an investigator finding, a possible operational implication, limits on its application, and supporting quotations.

The work proceeded in three parts. Development experiments tested passage selection and the form of the extracted lesson. The resulting method was applied to a corpus of 107 reports, followed by quotation validation and model-based curation. Finally, evidence-page retrieval methods were compared, and two simulation experiments examined navigation outcomes when raw passages or pilot lesson cards were provided to the agent. These experiments measure different things and are reported separately.
4.2 Source corpus and lesson-card format
4.2.1 Report collection and text index
An initial set of 147 PDFs was downloaded from MAIB search results for titles containing collision or grounding. After manual removal of attachments and documents outside the selected investigation reports, 107 PDFs remained. Together, they contained 1,761 pages. Text extraction produced at least 80 characters on 1,724 pages, of which 1,622 contributed at least one indexed chunk.

The existing retrieval index divided extracted text into chunks of at most 400 words, overlapping adjacent chunks by 60 words. Chunking was performed separately for each page; final fragments of fewer than 80 words were discarded. The resulting 2,373 chunks carried a report identifier, page number, and chunk identifier. The 400-word size, overlap, and minimum fragment length were fixed preprocessing settings. The supplied experiment record does not report an evaluation of alternative values.

For full-report extraction, all chunks belonging to a report were supplied together, in page order. Thus, chunking determined the text units and their provenance, whereas report grouping determined the model's input. Overlapping text was repeated in these inputs.

Table 4.1 — Source corpus and chunk index
Measure	Value
MAIB reports	107
PDF pages	1,761
Pages with at least 80 extracted characters	1,724
Pages represented in the chunk index	1,622
Page-bounded chunks	2,373
Maximum chunk size	400 words
Chunk overlap	60 words
Minimum retained fragment	80 words
Mean / median chunk length	281.2 / 307 words
Median chunks per report	20
Range of chunks per report	1–145


4.2.2 Lesson-card schema
A lesson card records one investigator finding and an interpretation of its relevance to navigation decisions. Its fields cover four categories:
Category	Recorded information
Source	Report identifier and title, card identifier, cited pages, and source quotations
Situation	Encounter type, vessel role, encounter phase, and warning signs
Reasoning	Causal mechanisms, investigator finding, and transferable lesson
Applicability	Non-transfer conditions and validation status


Encounter type, role, phase, and causal mechanism use controlled vocabularies, including fallback values for information that cannot be established from the text. Each card must contain one to three quotations with page references. The finding and lesson are generated interpretations; the quotations are the text against which provenance is checked. The schema defines what a card contains, not whether its interpretation is correct.
4.3 Development experiments
4.3.1 Lexical passage-selection pilot
The first experiment tested extraction from a small subset of each report. A deterministic scoring rule assigned five points for each detected analysis marker, three for each navigation marker, and up to three additional points for occurrences of collision. Examples of analysis markers concerned findings, conclusions, causal factors, and safety issues; navigation markers included lookout, CPA, TCPA, course, speed, and avoiding action. The eight highest-scoring positive passages were selected from each report; when no passage scored positively, two passages were supplied instead. The model could generate at most three cards per report.

These scores were a hand-set selection heuristic, not BM25. The weights (5, 3, and up to 3), eight-passage cutoff, and three-card limit were pilot configurations rather than parameters selected by a reported relevance study. A complete replication would also require the exact marker list and counting rule from the implementation.

The pilot produced 259 candidate cards from 107 reports. Quotation matching accepted 255 cards from 103 reports and rejected four. The accepted records contained 647 quotations. The run used 433,356 input and 71,469 output tokens, corresponding to approximately USD 0.79 at the recorded rates.

The 255 cards were then used as targets in an initial evidence-page retrieval test. A generic query was used to rank chunks within each card's known source report, and the result was scored against pages cited by that card. Hit@k denotes the proportion of cards with at least one cited page in the first k results; page recall@5 accounts for all cited pages; MRR and nDCG@5 also account for rank position (full definitions in §4.5.3). The results are retained for comparison with the later benchmark, but the target pages came from passages preselected by the lexical heuristic.

Table 4.2 — Pilot evidence-page retrieval (255 cards)
Method	Hit@1	Hit@3	Hit@5	Page recall@5	MRR	nDCG@5
BM25	0.318	0.698	0.863	0.698	0.536	0.518
BGE dense	0.200	0.522	0.745	0.580	0.417	0.399
Reciprocal-rank fusion	0.263	0.627	0.812	0.652	0.488	0.475
Fusion + cross-encoder	0.263	0.659	0.835	0.688	0.499	0.491


BM25 ranked highest on all reported pilot metrics. The cross-encoder increased fusion Hit@5 by 0.024, but the reported report-cluster bootstrap interval for this difference, [−0.035, 0.080], included zero. The same was true for the changes in MRR and nDCG@5. This pilot reranked 4,523 query–passage pairs in 118.7 CPU seconds. The later benchmark used cards extracted from complete report text rather than from preselected passages.
4.3.2 Applicability of raw passages to navigation states
A second experiment examined ten passage–state pairs collected from five evaluated navigation encounters. These contained four distinct retrieved passages. For each pair, an evaluator received the passage, the live navigation information, and the proposed decision, but not the earlier agent's assessment of the evidence. The evaluator classified the material and assessed whether the finding in the passage could inform the specific decision.

All ten pairs received a usefulness label of none. Six were classified as background or method material, one as investigator analysis, and three as mixed. In one recurring example, a passage about submarine target-motion analysis did not match a surface-vessel state in which contact position, speed, CPA, and TCPA were already available. This was a small, evaluator-labelled sample, but it exposed a mismatch between broad topical retrieval and the evidence required for a particular decision.
4.3.3 Decision-style extraction test
The third experiment reformulated the task. Five audited navigation states were used to describe the information available to the agent—such as relative bearing, range, CPA, TCPA, track quality, and control state—but a lesson did not have to match any one of those states. The four distinct passages from the preceding experiment were assessed for investigator findings that could inform navigation reasoning under some compatible circumstances.

Two passages yielded seven cards; two were classified as background material. The extracted findings concerned contact-speed estimation, convergence following own-ship turns, avoidance action, range uncertainty, contact classification, multiple-contact misclassification, and fishing-gear hazards. This test supplied the extraction formulation used for the complete corpus. Its four-passage size does not support a general estimate of extraction accuracy.
4.3.4 Choice of full-report processing
A passage-by-passage version of the task required 2,373 model requests and was estimated at 24.39 million input tokens, before completions. A separate screening step would reduce subsequent context but introduce another decision about which evidence was retained. The selected implementation grouped all indexed chunks by report, yielding 107 extraction requests. This also made it possible to generate and compare distinct findings in report context.
4.4 Full-report extraction and validation
4.4.1 Extraction
The model gpt-6.1-sol processed one report per Batch API request, using low reasoning effort and an initial completion limit of 12,000 tokens. Each request included the indexed text of the report and required a JSON response following the lesson-card schema. The instruction asked for distinct navigation-relevant investigator findings, a source-grounded lesson for each finding, supporting quotations with page numbers, and conditions limiting application of the lesson. Reports could return zero cards.
4.4.2 Quotation and structure checks
The validator checked JSON Schema conformance, the existence of cited pages in the report index, and quotation presence on the claimed pages after Unicode and whitespace normalisation. It also checked duplicate deterministic identifiers derived from the source report and normalised finding. A card failed if any of its required quotations failed to match. This produced source-validated cards: the method checked the existence of cited text, rather than independently judging the model's interpretation of that text.

In the initial extraction, 106 of 107 responses were parseable. They contained 1,015 cards; 998 passed validation, and 17 failed quotation matching. Four report responses contained no cards. One report produced no structured response after exhausting its 12,000-token completion allowance.

That report was rerun using the same 19 indexed chunks and a 20,000-token completion limit. The retry returned three candidates, of which two passed quotation validation. It occurred after the curation inputs had been fixed, so these two additional cards were not part of the curated benchmark set.
4.4.3 Model-based semantic curation
The 998 initially source-validated cards were submitted to a separate model assessment, grouped into 102 source-report sets. Each card received labels for three dimensions:
- Decision relevance: direct, adjacent, or out_of_scope.
- Grounding quality: strong, partial, or weak.
- Transfer quality: safe, review, or reject.
Direct meant that the finding concerned evidence or decisions available to the specified pre-collision navigation interface. Adjacent covered topics such as training, staffing, maintenance, and passage preparation; out_of_scope covered topics such as rescue, damage control, and unrelated technical safety. Strong required quotations to support the finding. The label safe meant that the proposed lesson was conditional and consistent with its stated limitations under the curator's criteria; it was not a measured safety outcome.

The predefined retention rule kept a card only if it was labelled direct, strong, and safe and was not marked as a duplicate. Other combinations entered the review or excluded sets according to the recorded rule. The curator had the card and its selected quotations, not the full source report. The labels therefore describe model-based curation decisions rather than independent expert verification.

All 998 cards received one curation decision: 503 were retained, 462 required review, and 33 were excluded. Relevance labels comprised 657 direct, 315 adjacent, and 26 out_of_scope cards; grounding labels comprised 750 strong, 241 partial, and seven weak cards. Transfer labels comprised 985 safe and 13 review, with none labelled reject; the curator flagged no within-report semantic duplicates. The retained records represented 101 reports and contained 1,008 quotations spanning 386 distinct report-page pairs. During curation, page lists were corrected for 127 cards that claimed pages not represented by a supporting quotation; the final retained records had no such page claims.

Table 4.3 — Extraction and curation counts
Measure	Result
Initial extraction requests	107
Initial parseable responses	106
Initial candidate cards	1,015
Initial source-validated cards	998
Initial rejected cards	17
Retry candidates / validated	3 / 2
Source-validated cards including retry	1,000
Verified quotations including retry	2,072
Cards supplied to curation	998
Retained / review / excluded	503 / 462 / 33
Reports in retained set	101
Quotations in retained set	1,008


4.4.4 Language-model usage and cost
The initial 107 report-level requests used 1,015,668 input and 295,521 output tokens (USD 2.49 at the recorded Batch rates). The retry used 7,522 input and 1,165 output tokens (USD 0.01); semantic curation used 295,812 input and 87,455 output tokens (USD 0.73). Total usage was 1,319,002 input and 384,141 output tokens, costing approximately USD 3.24. These figures cover model extraction and curation, not local text processing or navigation simulations.

Table 4.4 — Model usage and cost
Stage	Input tokens	Output tokens	Calculated cost
Initial extraction	1,015,668	295,521	USD 2.49
Retry	7,522	1,165	USD 0.01
Semantic curation	295,812	87,455	USD 0.73
Total	1,319,002	384,141	USD 3.24


4.5 Evidence-page retrieval benchmark
4.5.1 Retrieval task and query
The benchmark asked a specific question: given a report and a generic request for investigative evidence, which ranking method places the pages cited by its extracted cards near the top? It did not search across all 107 reports, and it did not use the current vessel state as a query. For each card, the candidate pool was restricted to chunks from that card's known source report.

Every card used the same query:
Locate report passages needed to construct a source-grounded maritime reasoning record. Prefer investigator analysis, conclusions, safety issues, causal or contributory factors, and passages connecting observed conditions and warning signs to decisions or actions and outcomes. Also find limitations that show when a lesson should not transfer to another encounter.

The query did not include words from the target card's finding, lesson, or quotations. Therefore, all cards belonging to the same report received the same chunk ranking, although their target pages differed. The benchmark measures within-report recovery of model-cited pages, not the ability to select the right lesson for a live encounter.
4.5.2 Ranking methods and parameter settings
Four ranking configurations were compared on the same candidate pools.

BM25 (lexical retrieval). BM25 scores a passage using the occurrences of query terms, how common the terms are across candidate passages, and passage length. A standard BM25 form is
\[
S(q,d)=\sum_{t\in q}\operatorname{IDF}(t)
\frac{f(t,d)(k_1+1)}{f(t,d)+k_1\left(1-b+b\frac{|d|}{\overline{|d|}}\right)}.
\]
Here, \(f(t,d)\) is the frequency of query term \(t\) in chunk \(d\); \(|d|\) is that chunk's length; \(\overline{|d|}\) is the average length of chunks in the candidate set; and IDF gives more weight to terms that occur in fewer chunks. The reported implementation scored chunks within the current report, with \(k_1=1.5\) and \(b=0.75\). Parameter \(k_1\) controls how quickly repeated occurrences of a term stop contributing additional score: increasing it makes term frequency matter for longer. Parameter \(b\) controls passage-length normalisation, from none at \(b=0\) to full normalisation at \(b=1\). These were fixed settings; no tuning or sensitivity analysis is reported. The exact tokenizer and BM25/IDF implementation would need to be specified from code for full reproducibility. \cite{robertson2009bm25}

Dense retrieval (semantic embeddings). BAAI/bge-small-en-v1.5 mapped the query and each chunk into vectors. The vectors were L2-normalised, then ranked by cosine similarity; for unit-length vectors, cosine similarity equals their dot product. Unlike BM25, the ranking depends on similarity in the embedding space rather than exact query-term matches. The source draft identifies the model but does not document its text preprocessing or any query prefix used by the implementation. \cite{reimers2019sentencebertsentenceembeddingsusing,xiao2024cpack}

Reciprocal-rank fusion (RRF). RRF combined the complete BM25 and dense rankings by adding a rank-based score for each chunk:
\[
\operatorname{RRF}(d)=\frac{1}{60+r_{\mathrm{BM25}}(d)}
+\frac{1}{60+r_{\mathrm{dense}}(d)}.
\]
Here, \(r\) is the one-based rank of a chunk in a method's list. The constant 60 reduces the advantage of the very first positions and controls how strongly rank differences affect the fused result. It was a fixed fusion parameter, not a value tuned on this MAIB dataset. RRF combines orders, not the raw BM25 and cosine scores. \cite{cormack2009rrf}

Cross-encoder reranking. The model cross-encoder/ms-marco-MiniLM-L6-v2 scored each query–chunk pair in the first 20 positions of the RRF ranking, and these candidates were reordered using those scores. Unlike an embedding model, a cross-encoder jointly reads the query and candidate passage. The 20-chunk depth determined how many candidates incurred this additional computation; a chunk outside the first 20 could not be promoted by this reranker. The supplied record does not report any search over alternative reranking depths or models. \cite{nogueira2019passagererankingbert}

For all four methods, repeated chunks from the same page were collapsed to one page result by retaining that page's first occurrence in the ranked list.
4.5.3 Evaluation metrics
Let \(G_c\) be the set of cited pages for card \(c\), and let \(P_c[1\!:\!k]\) be the first \(k\) distinct pages returned for its source report.
- Hit@k: equals 1 if any page in \(G_c\) occurs among the first \(k\) returned pages, and 0 otherwise. Results are averaged over cards.
- Reference-page recall@5: the proportion \(|G_c\cap P_c[1\!:\!5]|/|G_c|\), averaged over cards. Unlike Hit@5, it accounts for cards citing several pages.
- Mean reciprocal rank (MRR): the average of \(1/r_c\), where \(r_c\) is the position of the first cited page (zero contribution if none is found).
- nDCG@5: a rank-discounted measure that assigns higher scores when more cited pages appear earlier in the first five results. The implementation's relevance convention should be reported from code; the draft implies binary page relevance but does not specify the formula.
The final benchmark contained 503 cards from 101 reports. Cards from one report shared a ranking, so they were not independent retrieval trials. For the difference between cross-encoder reranking and RRF, uncertainty was estimated through 2,000 paired bootstrap resamples at the report level (seed 20261113). These intervals quantify variation under that resampling scheme; they are not expert judgments about lesson quality.
4.6 Results of the final retrieval benchmark
Table 4.5 — Within-report retrieval on the 503 retained cards
Method	Hit@1	Hit@3	Hit@5	Page recall@5	MRR	nDCG@5
BM25	0.139	0.423	0.652	0.561	0.349	0.355
BGE dense	0.113	0.358	0.565	0.489	0.314	0.308
BM25 + BGE RRF	0.125	0.429	0.592	0.522	0.336	0.336
RRF + cross-encoder	0.109	0.406	0.604	0.517	0.324	0.322


BM25 had the highest Hit@1, Hit@5, page recall@5, MRR, and nDCG@5. RRF had a slightly higher Hit@3 (0.429 versus 0.423). BM25's Hit@5 of 0.652 means that at least one quoted source page was among the first five distinct retrieved pages for 65.2% of the evaluated cards. Its page recall@5 of 0.561 reflects the additional difficulty of finding all cited pages for cards supported by more than one page.

Reranking raised RRF Hit@5 from 0.592 to 0.604, a difference of 0.012. The report-cluster bootstrap 95% interval for this difference was [−0.055, 0.075]. Reranking decreased MRR by 0.012 (interval [−0.041, 0.017]) and nDCG@5 by 0.014 (interval [−0.051, 0.024]). These results do not show a consistent improvement from the cross-encoder on the specified benchmark.

The cross-encoder scored 9,082 query–chunk pairs in 236.6 seconds on CPU. The draft also records 4.7 seconds for dense query encoding; that is a narrower operation than cross-encoder scoring of all candidate pairs and should not be treated as an end-to-end latency comparison.

The BM25 Hit@5 score fell from 0.863 in the pilot to 0.652 in the final benchmark. The pilot's target pages were obtained from passages selected using lexical markers, whereas the final targets came from report-wide extraction and curation. Consequently, these two evaluations use different target construction procedures. The observed difference cannot be attributed solely to a change in retrieval method or model performance.
4.7 Closed-loop navigation experiments
The two simulation comparisons examined navigation outcomes, not evidence-page recovery. Their interventions and execution conditions differed, so their results are presented as separate experiments.
4.7.1 Raw passages versus pilot cards
In the first 22-encounter comparison, the captain critic received either the two highest-ranked raw MAIB passages or the two highest-ranked cards from the 255-card pilot corpus. Both conditions used the same set of 22 Imazu encounters and the same frozen evaluation function. The composite score weighted safety (0.35), regulatory compliance (0.10), COLREG citation agreement (0.10), temporal efficiency (0.10), spatial efficiency (0.10), manoeuvre economy (0.10), and smoothness (0.15). Citation agreement concerned COLREG rules, not MAIB quotation accuracy.

Table 4.6 — First 22-case comparison
Condition	Composite	Safety	Compliance	Citation agreement	Temporal	Spatial	Manoeuvre	Smoothness	Verdicts
Raw MAIB passages	0.705	0.682	0.718	0.004	0.820	0.830	1.000	0.986	12 passes; 9 CPA violations; 1 collision
Pilot lesson cards	0.717	0.747	0.696	0.001	0.850	0.866	0.956	0.989	13 passes; 7 CPA violations; 2 collisions


The cards increased mean composite score by 0.012 and mean safety by 0.065, while reducing CPA-violation verdicts from nine to seven. However, collision verdicts increased from one to two and compliance decreased from 0.718 to 0.696. The reported difference is mixed and concerns the pilot cards, not the 503-card curated corpus. The full component scores are included in Table 4.6. Citation agreement is consistently near zero in these results and requires the scoring rule and its implementation to be documented.
4.7.2 Base narrative versus raw MAIB passages with critic
The second experiment used lock-step simulation. Model calls paused simulation time, and decisions were requested every 30 simulated seconds during the encounter window. A base narrative condition was compared with a condition in which the captain critic received two retrieved raw MAIB passages. Both were evaluated on 22 encounters.

Table 4.7 — Lock-step comparison
Condition	Composite	Safety	Compliance	Citation agreement	Temporal	Spatial	Manoeuvre	Smoothness	Verdicts
Base narrative	0.828	0.928	0.677	0.020	0.863	0.995	1.000	0.985	17 passes; 5 CPA violations
Raw MAIB passages with critic	0.825	1.000	0.564	0.001	0.884	0.976	0.831	0.996	22 passes


The raw-MAIB-with-critic condition produced 22 ordinary passes and no CPA-violation verdicts under the simulation's 500 m clearance criterion. It also recorded lower compliance (0.564 versus 0.677), lower manoeuvre economy (0.831 versus 1.000), and a slightly lower composite score (0.825 versus 0.828). The safety score is a value from the stated evaluation function, not a general probability of safe navigation.

The intended critic-without-evidence control completed only 11 of 22 encounters. The available comparison therefore does not isolate the effect of the retrieved MAIB passages from the effect of introducing the critic model.
4.8 Discussion and answer to RQ1
The extraction experiment converted indexed text from 107 MAIB reports into 1,000 source-validated candidate cards, including the retry. Model-based curation assessed the 998 cards available before that retry and retained 503 cards from 101 reports. Each retained card contains a structured interpretation supported by quotations matched to source pages. These counts describe the implemented transformation and its validation stages.

The retrieval benchmark concerns a narrower task: recovering a card's cited pages from its already-identified report using a generic evidence query. On that task, BM25 performed best on most reported metrics. This result supports BM25 as the tested within-report evidence-page baseline. It does not establish that BM25 is the best method for selecting reports or lesson cards from a live navigation state, because neither report selection nor state-to-card retrieval was evaluated by that benchmark.

The navigation experiments show different operational outcomes. The pilot card condition improved some aggregate scores but recorded more collisions than the raw-passage condition. In a separate lock-step comparison, adding a critic with raw MAIB passages eliminated CPA-violation verdicts but lowered compliance and manoeuvre economy. That second experiment also lacks a completed critic-without-evidence control. Neither result measures the closed-loop effect of the final 503-card corpus.

For RQ1, the established result is a page-traceable text-to-card procedure: index the reports, process complete report-level text, generate schema-constrained cards, validate cited quotations, and apply documented curation criteria. The remaining evaluation questions are distinct: the semantic accuracy of the cards, retrieval of relevant cards from live vessel states, and the effect of those cards on navigation decisions.
4.9 Reproducibility details to complete before submission
The chapter's figures and tables can be reproduced only if the implementation-specific settings below are recorded. The current draft does not provide them:
1. Exact lexical pilot marker lists, matching/counting rules, tie-breaking, and fallback selection order.
2. BM25 library/version, tokenizer, stopword or stemming behaviour, and IDF variant; confirm that report-local statistics were used.
3. BGE query preprocessing or instruction prefix, passage preprocessing, embedding setup, and whether embeddings were cached.
4. Cross-encoder candidate tie handling, scores and ordering for the first 20 results, and the precise procedure for deduplicating pages.
5. The nDCG relevance convention, any cutoffs used for MRR, and bootstrap confidence-interval construction.
6. Imazu encounter specification, retrieval configuration inside both navigation comparisons, simulation timing and scoring implementation, and the 500 m threshold's role.
These are reproducibility requirements, not grounds for inventing implementation details. They should be populated from the source code and experiment logs.