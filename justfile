default:
    @just --list

fetch:
    uv run amigo3 fetch

build *args:
    uv run amigo3 build {{args}}

solr:
    docker compose up -d

# Alternative when Docker isn't running; requires Java 17+.
solr-local:
    uv run python scripts/solr_local.py start

solr-stop:
    uv run python scripts/solr_local.py stop

publish:
    uv run amigo3 publish

# Local testing includes chat; install claude-agent-acp as described in README.
serve *args:
    uv run --extra chat amigo3 serve --chat-command 'claude-agent-acp' {{args}}

local *args:
    uv run --extra chat amigo3 serve --backend duckdb --chat-command 'claude-agent-acp' {{args}}

check:
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest

# Captioned feature tours of a running server: screenshots in docs/images; MP4s and GIFs in showcase/.
showcase *args:
    uv run python scripts/showcase.py {{args}}
