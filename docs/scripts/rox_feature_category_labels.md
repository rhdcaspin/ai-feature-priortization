# `rox_feature_category_labels.py`

## Role

Uses a **local Ollama** model to classify each selected **ROX Feature** into exactly one **product pillar** Jira label (mutually exclusive set of three slugs) and to set a boolean **`enterprise_ready`** label. Compares current Jira labels to the model output and applies **add/remove** label updates via the Jira REST API (unless `--dry-run`).

## Prerequisites

- Running Ollama (`ollama serve`) and a pulled model; set `OLLAMA_MODEL` or pass `--ollama-model`.
- Jira credentials in `.env` (same family as `jira_feature_validator.py`).
- Optional: `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT`, `--ollama-no-json-format` if the model returns weak JSON.

## Common commands

```bash
# Preview planned label changes (no Jira writes)
OLLAMA_MODEL=llama3.2 python3 rox_feature_category_labels.py --dry-run

# Apply after reviewing dry-run
python3 rox_feature_category_labels.py --apply --ollama-model mistral

# Custom JQL selecting which features to process
python3 rox_feature_category_labels.py --apply --jql 'project = ROX AND type = feature AND "Target Version" = "5.0.0"'
```

Exactly **one** pillar label is enforced on each issue: any other pillar from the set is removed when it differs from the model’s choice. If the model returns a compound value (for example two slugs separated by `|`), the script keeps the first recognizable slug. Plain-line fallback does **not** set `enterprise_ready`; JSON paths should include `enterprise_ready` when you want that label synced.

Run `python3 rox_feature_category_labels.py --help` for batch size, delays, and retry flags.
