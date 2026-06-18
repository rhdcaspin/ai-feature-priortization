# rox_feature_category_labels.py

AI-classifies ROX features into product-pillar labels using a local Ollama model, then syncs Jira labels. Also sets the `enterprise_ready` boolean label.

## Pillar labels (mutually exclusive)

- `unified-workload-protection`
- `frictionless-security-runtime-observability`
- `ai-driven-vuln-risk-management`
- `enterprise-scalability-support`

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--jql` | ROX features TV 5.0.0 | — | JQL selecting features to classify |
| `--dry-run` | `False` | — | Print planned changes only |
| `--apply` | `False` | — | Write label changes to Jira |
| `--batch-size` | `8` | — | Features per LLM request |
| `--desc-max` | `3500` | — | Max description chars per feature sent to model |
| `--delay` | `1.0` | — | Seconds between LLM batches |
| `--no-single-retry` | `False` | — | Skip single-issue retry when batch omits keys |
| `--no-plain-fallback` | `False` | — | Skip plain-line (non-JSON) LLM fallback |
| `--ollama-url` | `http://127.0.0.1:11434` | `OLLAMA_BASE_URL` | Ollama server URL |
| `--ollama-model` | — | `OLLAMA_MODEL` | Ollama model name (required) |
| `--ollama-timeout` | `600` | `OLLAMA_TIMEOUT` | Seconds per Ollama request |
| `--ollama-no-json-format` | `False` | — | Don't use Ollama `format=json` |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Jira URL |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `OLLAMA_MODEL` | Yes | Model name (e.g. `llama3.2`, `mistral`) |
| `OLLAMA_BASE_URL` | No | Default: `http://127.0.0.1:11434` |
| `OLLAMA_TIMEOUT` | No | Default: `600` seconds |

## Data flow

1. Connects to Jira via `JiraFeatureValidator`
2. Fetches features matching JQL (summary, description, labels)
3. Sends batches to Ollama `/api/chat` (or `/api/generate` on older Ollama)
4. Parses JSON response to get `{key, category, enterprise_ready}` per issue
5. On missing keys: retries single-issue with JSON, then without JSON format, then plain-line prompt
6. Computes label updates: removes wrong pillar labels, adds correct one, adds/removes `enterprise_ready`
7. With `--apply`: writes label updates to Jira via REST API

## Dependencies

- `jira_auth.py`, `jira_feature_validator.py` (connection, ADF text extraction)
- Running Ollama server with a pulled model
