# JobScout

JobScout is a small local application that collects public job postings from configured Greenhouse, Lever and Ashby boards or explicitly seeded public job pages, normalizes them, and ranks them against TOML-based user profiles. Ranking is deterministic and works without an LLM.

## Requirements

- macOS or Windows
- [uv](https://docs.astral.sh/uv/)

The project pins Python 3.13 in `.python-version`; `uv` can install and manage it independently of the system Python.

## Setup

```text
uv sync
```

Copy the example configuration without committing the local copy:

macOS:

```text
cp config/jobscout.example.toml config/jobscout.toml
```

Windows PowerShell:

```powershell
Copy-Item config/jobscout.example.toml config/jobscout.toml
```

Edit `config/jobscout.toml`, enable sources, and replace their board identifiers. Database paths are resolved relative to the configuration file.

Validate the configuration:

```text
uv run jobscout config check
```

## Usage

Collect and rank all enabled sources:

```text
uv run jobscout scan
```

Restrict a scan if needed:

```text
uv run jobscout scan --profile friend-a --source example-greenhouse
```

Start the local dashboard:

```text
uv run jobscout serve
```

Open `http://127.0.0.1:8000`. The server intentionally binds to localhost by default and has no authentication.

Configuration precedence is `--config`, then `JOBSCOUT_CONFIG`, then `config/jobscout.toml`. Use absolute paths when running through an operating-system scheduler.

## Development

```text
uv run pytest
```

Default tests use recorded fixtures and `httpx.MockTransport`; they do not contact live ATS APIs.

## Data and privacy

The repository is public. `config/jobscout.toml`, databases, WAL files and logs are ignored by Git. Only `config/jobscout.example.toml` is intended for version control. Back up the SQLite database before changing schema or upgrading beyond the MVP.

Scheduling examples for macOS and Windows are in [docs/scheduling.md](docs/scheduling.md).
