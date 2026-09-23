# Google News decode: Python activity vs gnews-decoder 0.1.0 (2026-09-23)

Stale by default. What it settled: the `decodeLinks` activity moved from the Python worker
(googlenewsdecoder fork) to the TypeScript worker (`digest/src/activities/gnews-decode.ts` over
gnews-decoder 0.1.0).

**Result: 72 of 72 links decode to the same publisher URL; outcome and `attempted` match on all six runs.**
All 72 URLs also appear in the issues production published for those runs (the Python, on the
production address, on the day). 64 Reuters, 8 Nikkei Asia; 71 unique links, one shown on two days.

| run | links | Python | TypeScript |
|---|---|---|---|
| 300 | 12 | completed, 12 decoded | completed, 12 decoded |
| 301 | 19 | completed, 19 | completed, 19 |
| 302 | 11 | completed, 11 | completed, 11 |
| 303 | 10 | completed, 10 | completed, 10 |
| 304 | 9 | completed, 9 | completed, 9 |
| 305 | 11 | completed, 11 | completed, 11 |

Measured from one residential address, Python first, then TypeScript, within the hour; no 429.
The rate-limit, deadline and cancel paths are covered by `gnews-decode.test.ts` only, not live.

## Files

- `plan.json`: `newsroom/tools/gnews_oracle.py` over the prod clone (`data/prod-20260923b.db`), runs
  300-305. Each run's `render` list is the links production decoded.
- `python-decode.json`: the Python activity over each run's links, one call per run, live.
- `typescript-decode.json`: the TypeScript activity over the same links, live.

## Reproduce

```sh
docker compose -p digest-gnews run --rm -T -v "$PWD/data/prod-20260923b.db:/db/prod.db:ro" \
  -e PYTHONPATH=/app/newsroom/src --entrypoint python3 ci newsroom/tools/gnews_oracle.py /db/prod.db 300 301 302 303 304 305 > plan.json
# the Python side, at the commit that added this directory (the activity is gone after it):
docker compose -p digest-gnews run --rm -T -v "$PWD/digest/python/decode_oracle.py:/app/src/decode_oracle.py:ro" \
  -v "$PWD/docs/proposed/2026-09-23-gnews-parity:/parity" ci-python python decode_oracle.py /parity/plan.json > python-decode.json
# the TypeScript side, any time after:
docker compose -p digest-gnews run --rm -T -e GNEWS_LIVE=1 -e GNEWS_ORACLE=/parity/python-decode.json \
  -e GNEWS_PARITY_OUT=/parity/typescript-decode.json -v "$PWD/docs/proposed/2026-09-23-gnews-parity:/parity" \
  ci-ts npx vitest run src/activities/gnews-decode.parity.test.ts
```

Each live run spends about 70 decodes of the address's daily Google News budget.
