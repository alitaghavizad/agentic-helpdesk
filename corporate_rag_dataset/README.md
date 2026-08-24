# Corporate RAG Synthetic Dataset

This dataset contains **100 synthetic employee profiles** and **25 synthetic helpdesk profiles**. Each profile is stored as an independent Markdown document for direct ingestion into a RAG pipeline.

## Structure

- `employees/` — 100 employee Markdown documents
- `helpdesk/` — 25 helpdesk Markdown documents
- `evaluation/queries.jsonl` — 60 retrieval evaluation queries
- `evaluation/qrels.csv` — graded query-document relevance judgments
- `evaluation/README.md` — metric and evaluation guidance

All names, IDs, email addresses, organizations, support histories, devices, and scenarios are fictional.

## Ingestion recommendation

Use the Markdown filename as the stable `document_id`. Preserve it as metadata even if you chunk the document. For document-level retrieval evaluation, collapse multiple retrieved chunks that share the same parent document before calculating metrics.


## Expanded profile size

The person profiles were expanded for more realistic RAG chunking and retrieval experiments.

- Shortest person profile: 7219 characters
- Average person profile: 7568 characters
- Longest person profile: 7731 characters

The evaluation document IDs remain unchanged, so the existing `queries.jsonl` and `qrels.csv` are still valid.
