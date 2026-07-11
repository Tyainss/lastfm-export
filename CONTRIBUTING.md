# Contributing to lastfm-export

Thank you for contributing to `lastfm-export`.

This guide covers the expected development workflow. For installation and usage, see [README.md](README.md).

## Before contributing

Before opening an issue or pull request:

* check whether the topic has already been reported
* keep credentials and personal data out of logs, screenshots, and examples
* keep each contribution focused on one problem

A useful bug report should include:

* expected behavior
* actual behavior
* steps to reproduce
* the error message or traceback
* operating system, Python version, and package version

Never include API keys, client secrets, access tokens, `.env` contents, or private listening data.

## Development workflow

Most changes should follow this flow:

1. Create a branch from the latest `main`.
2. Make one focused change.
3. Add or update tests when behavior changes.
4. Update documentation or `CHANGELOG.md` when users need to know about the change.
5. Run the local checks.
6. Open a pull request into `main`.
7. Link the related issue when applicable.

Avoid combining unrelated fixes, features, dependency updates, and refactors in the same pull request.

Normal contribution branches should not update the package version or create release tags.

## Branch names

Use short, clear branch names.

```text
feature/short-description
fix/short-description
docs/short-description
chore/short-description
release/vX.Y.Z
```

Examples:

```text
feature/spotify-match-options
fix/lastfm-private-listening-403
docs/update-authentication
chore/refresh-dependencies
```

Use `release/` branches only for release preparation.

## Commit messages

Use short commit messages that explain the purpose of the change.

Recommended format:

```text
:emoji: Short description
```

Recommended prefixes:

| Prefix        | Emoji | Use for                                  | Example                                               |
| ------------- | ----- | ---------------------------------------- | ----------------------------------------------------- |
| `:sparkles:`  | ✨     | New features or user-facing improvements | `:sparkles: Add configurable Spotify lookup behavior` |
| `:memo:`      | 📝    | Documentation                            | `:memo: Update authentication documentation`          |
| `:wrench:`    | 🛠️   | Configuration, tooling, or CI/CD         | `:wrench: Enforce Ruff formatting in CI`              |
| `:test_tube:` | 🧪    | Tests                                    | `:test_tube: Add Last.fm client tests`                |
| `:bug:`       | 🐛    | Bug fixes                                | `:bug: Fix resume watermark handling`                 |
| `:rocket:`    | 🚀    | Release preparation                      | `:rocket: Prepare v0.1.2 release`                     |
| `:package:`   | 📦    | Package publishing or dependency changes | `:package: Update requests dependency`                |
| `:truck:`     | 🚚    | Moving or renaming code or files         | `:truck: Move CLI helpers into package layout`        |
| `:fire:`      | 🔥    | Removing code or files                   | `:fire: Remove unused export helper`                  |
| `:recycle:`   | ♻️    | Refactoring without behavior changes     | `:recycle: Simplify recent-track parsing`             |
| `:lock:`      | 🔒    | Security-related changes                 | `:lock: Avoid exposing credentials in errors`         |

Examples:

```text
:bug: Handle hidden Last.fm listening history
:test_tube: Add Last.fm access error tests
:memo: Document Last.fm privacy requirement
:wrench: Enforce Ruff formatting in CI
```

Keep commits logically focused. Separate implementation, tests, documentation, and CI changes when that makes the history clearer.

## Local setup

Install the project and development dependencies:

```bash
uv sync --dev
```

To enable `.env` loading:

```bash
uv sync --dev --extra dotenv
```

Supported environment variables include:

```env
LASTFM_API_KEY=""
LASTFM_USERNAME=""
SPOTIFY_CLIENT_ID=""
SPOTIFY_CLIENT_SECRET=""
```

Spotify credentials are only required for Spotify enrichment.

Do not commit `.env` files or real credentials.

## Dependencies

Use `uv` to manage dependencies.

```bash
uv add package-name
uv add --dev package-name
uv add --optional extra-name package-name
```

When dependency resolution changes, commit both:

```text
pyproject.toml
uv.lock
```

Do not edit `uv.lock` manually. Avoid adding dependencies unless they provide clear value.

## Code quality checks

Before opening a pull request, run:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

To reproduce the CI formatting check without modifying files:

```bash
uv run ruff format --check .
```

If package metadata, dependencies, imports, or package structure changed, also run:

```bash
uv build --no-sources
```

## Testing

Tests live under `tests/unit/` and mirror the package structure.

Add tests to an existing test module when the changed behavior already belongs there. Create a new test module only for a new component or responsibility.

Tests must not call real external services, including Last.fm, Spotify, PyPI, or TestPyPI.

Use mocks, fake clients, static payloads, `responses`, `monkeypatch`, `CliRunner`, and temporary files.

Add or update tests when changing:

* HTTP retries or error handling
* Last.fm pagination and parsing
* Spotify enrichment
* CLI arguments, messages, or exit codes
* file formats and resume behavior
* date or timestamp handling
* public package behavior

Prefer test names that describe the behavior being protected.

## Documentation and changelog

Update:

* `README.md` when installation, authentication, usage, or output behavior changes
* `CONTRIBUTING.md` when the development workflow changes
* `CHANGELOG.md` for user-visible changes

Add user-visible changes under `## [Unreleased]`.

Tests, formatting, CI-only changes, and internal refactors normally do not need changelog entries.

## Pull requests

Open pull requests into `main`.

A pull request should include:

* a short summary of the problem and solution
* the main behavior changed
* the checks that were run
* any known limitations
* the related issue, when applicable

Use a closing keyword when the pull request fully resolves an issue:

```text
Fixes #123
Closes #123
Resolves #123
```

Keep the issue open until the pull request is merged.

## Releases

Only maintainers should update package versions, create release branches, or push release tags.

`lastfm-export` follows semantic versioning:

* PATCH for backward-compatible fixes
* MINOR for backward-compatible features
* MAJOR for breaking changes

Production tags use:

```text
vX.Y.Z
```

TestPyPI tags use:

```text
test-vX.Y.Z
```

Publishing is handled by the GitHub Actions workflows in `.github/workflows/`.
