# Gate 1

Passed: True

Retrieval top 5: 96.7%

Extraction: 216/240 cells; 5 flagged images; 0 unflagged wrong numbers.

All 24 wrong or missing cells belong to flagged unreadable images (rates_17.png and rates_19.png); no unflagged incorrect cells were accepted.

Chunks: 70

Pacific-day usage: {'embed_requests': 101, 'vlm_requests': 38}

Failures: []

Unreadable images: ['rates_17.png', 'rates_19.png']

Limitations: Optional reranker is disabled and was not exercised. Both providers returned rate limits for some images; cached unreadable results remain flagged, not accepted extractions.

## Targeted retry

After a three-minute cooldown, only the two failed images were retried in ingest mode; successful results are now reused from cache.

| Image | Expected cells correct | Complete-row lookup | State |
|---|---:|---|---|
| rates_17.png | 12/12 | 12m=6.67%, 24m=6.77%, 36m=6.87% | done |
| rates_19.png | 12/12 | 12m=6.69%, 24m=6.79% | done |

The cut-off row in rates_19.png remains null and flagged, and is excluded from lookup. Fixture rows were checked in isolation; the Atlas bank 12-month general FD rate remains 7.10%. No bank document was re-ingested.
