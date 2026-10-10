# Repository Guidelines

## Project Structure & Module Organization

Chintu is currently a design-only project for voice-controlled Claude/Codex tasks on Ubuntu through an Android companion and Tailscale. `docs/` contains seven numbered Markdown documents covering requirements, architecture, audio, agent contracts, security, delivery, and decisions. No source code, tests, or assets exist yet.

Start with `docs/01-requirements.md`. Treat `docs/07-decisions-and-sources.md` as the authoritative decision register. The proposed implementation layout in `docs/02-architecture.md` includes `apps/android/`, `services/gateway/`, `packages/agent-adapters/`, `tests/`, `deploy/`, and `config/`; these directories are not implemented.

## Build, Test, and Development Commands

There are no configured build, runtime, package-management, or automated test commands. Use these documentation inspection commands from the project root:

- `rg --files docs` — list design documents.
- `rg -n 'FR-|NFR-|D[0-9]+|Q[0-9]+' docs` — locate requirements, decisions, and open questions.

Document actual setup and verification commands when implementation is authorized; do not present proposed dependencies as installed tooling.

## Coding Style & Naming Conventions

Follow the existing Markdown style: descriptive headings, short paragraphs, hyphen bullets, tables for comparable facts, and relative links between documents. Preserve the `NN-kebab-case.md` naming pattern, requirement IDs (`FR-01`, `NFR-01`), and decision/question IDs (`D1`, `Q1`). Keep confirmed facts, proposals, and measured evidence distinct.

No code indentation standard, formatter, or linter is configured. Establish language-specific conventions when Python/Kotlin implementation begins.

## Testing Guidelines

For documentation changes, check relative links and consistency across requirements, contracts, decisions, and milestones. Update the validation matrix in `docs/06-delivery-plan.md` when behavior changes. No testing framework, test naming convention, or coverage threshold exists yet. Future tests should cover submission retries, recovery, approvals, cancellation, and physical-device audio behavior described in that matrix.

## Commit & Pull Request Guidelines

This directory has no Git metadata or commit history, so no existing commit convention can be inferred. When version control is introduced, use concise imperative subjects, such as `docs: clarify cancellation outcomes`. Pull requests should describe the change, affected requirement/decision IDs, validation performed, and unresolved questions. Include screenshots when changing a future UI.

## Security & Scope

Keep work documentation-only until implementation is requested. Never commit credentials or raw audio. Preserve task-scoped authorization and distinguish stopping speech from cancelling execution, as specified in the design documents.
