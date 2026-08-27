# Evidence bundle

Everything here is generated, not written by hand. Regenerate it with:

```bash
python -m scripts.run_experiment --cohort-size 200 --seed 3 \
    --replicate 11,29,47 --out-dir artifacts --freeze
```

| File | What it is |
|---|---|
| `report.md` | The full report: headline, band sweep, replication across four cohorts, the baseline it compared against, and every assumption it rests on |
| `replication.json` | Per-cohort findings, machine readable |
| `sweep.json` | The Low/Mid/High sweep for the registered cohort |
| `frozen_config.json` | Configuration hash, written *before* the run |
| `llm_cache.json` | Every model response, keyed by prompt hash |
| `audit_mid_*.csv` | Full audit trail for both arms at the Mid band, one row per decision |
| `console.html` | The decision console, rebuilt from the same run |

## Reproducing without an API key

`llm_cache.json` holds all 36 model responses the run needed. Copy it to your
output directory and the experiment replays exactly, with no key and no network:

```bash
mkdir -p artifacts && cp evidence/llm_cache.json artifacts/
RECOUP_LLM_MODEL=openai/gpt-oss-120b \
  python -m scripts.run_experiment --cohort-size 2000 --seed 3 \
      --replicate 11,29,47 --out-dir artifacts
```

**Name the model.** A cache key is a hash of `model | system | user |
max_tokens`, so replaying a cache means asking as the model that filled it. Without
`RECOUP_LLM_MODEL` a keyless run falls back to a different default, every key
misses, and the runner refuses to publish rather than quietly measuring the
deterministic planner instead.

This was verified: the command above produces `sweep.json` and
`replication.json` byte-identical to the files here, with every provider key
unset. That is the whole point of hashing the prompt rather than trusting a model
to be deterministic.

## Reading the audit CSVs

One row per decision. `stage` is `ingest`, `classify`, `plan`, `execute`,
`policy_block`, `ladder_block` or `terminal`. Every `execute` row names the rule
that permitted it; every block row names the rule that refused it. Filter by
`subscription_id` to replay one customer's entire story.
