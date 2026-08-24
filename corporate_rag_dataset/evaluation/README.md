# Retrieval Evaluation Guide

`queries.jsonl` contains `query_id`, the natural-language `query`, `relevant_docs`, a `graded_relevance` mapping, and a reference `answer`.

`qrels.csv` contains one query-document relevance judgment per row and can be used with common information-retrieval evaluation tooling.

## MRR
For every query, identify the rank of the first relevant document. Reciprocal rank is `1 / rank`; MRR is the mean across all queries. Queries with a single intended document are particularly useful for MRR.

## Precision@K
`relevant documents in top K / K`. This measures how much of the retrieved top-K set is actually useful.

## Recall@K
`relevant documents in top K / total known relevant documents`. Multi-document department and tool queries are included specifically to make Recall@K meaningful.

## nDCG@K
Use the `graded_relevance` values as gains. Compute DCG from your retrieved ranking and divide it by the ideal DCG for that query. Higher relevance grades should contribute more than lower grades.

## Chunking warning
If your retriever returns chunks rather than source documents, preserve `parent_document_id`. Collapse duplicate chunks from the same source before document-level evaluation; otherwise multiple chunks from one employee may inflate or distort retrieval metrics.

## Suggested K values
Evaluate at K = 1, 3, 5, and 10. MRR is usually reported without K, while Precision@K, Recall@K, and nDCG@K should explicitly state K.
