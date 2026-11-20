# OKF data layer

Design v0.2 · 18 November 2026 · Decision D22

Chintu writes the data people keep using after a session — dictated voice notes today — as [OKF v0.2](#format) Markdown files, the same format the owner's other automations read and write. Other tools read these files directly from disk. They never need Chintu's database or API. Operational state (pairing, tokens, tasks, events) stays in SQLite and is never written as OKF.

## Format

- A **bundle** is a folder with `index.md` (YAML frontmatter `okf_version: "0.2"`) and `log.md` at its root. Both are reserved: they are not concept files. Dot-files and dot-folders are ignored.
- A **concept file** is YAML frontmatter between `---` lines, then a Markdown body. `type` is required. `id`, where present, equals the file name without `.md`.
- `status` is reserved for the document lifecycle (`draft`, `stable`, `deprecated`). Open or closed work uses `state`.
- `generated: {by, at}` records the writer (`process:<name>` or `agent:<name>`) and time. `sources` is a list of `{resource, title}`; `resource` is required.
- Times are ISO 8601 with an offset. Lists use `tags`, not `tag`.
- Unknown keys are kept by every reader and writer.

Chintu writes frontmatter values as JSON scalars, which are valid YAML, so the gateway needs no YAML library (`services/gateway/src/chintu_gateway/okf.py`). Files are written atomically with mode `0600`.

## Voice notes

The notes folder is a bundle. It is `notes_dir` from the operator config when set; otherwise `$DATA_HOME/00-Inbox/Notes` when the machine has a `DATA_HOME` (the environment, or a `DATA_HOME=<path>` line in `~/.config/ataria/data.env`), the machine's copy of the shared vault that Ataria syncs; otherwise `notes/` beside the gateway database. Each finished, non-empty note is one file, `YYYY-MM-DD-HHMM-<note id prefix>.md`:

```markdown
---
type: "Chintu Note"
id: "2026-11-18-2104-2c0f1591"
title: "Note 2026-11-18 21:04"
created_at: "2026-11-18T15:34:19.710736Z"
finished_at: "2026-11-18T21:04:19+05:30"
words: 5
generated:
  by: "process:chintu-gateway"
  at: "2026-11-18T21:04:19+05:30"
sources:
  - resource: "chintu://note/2c0f1591-a2ba-4993-b42e-d4e74373780c"
    title: "Voice note from Pixel"
---

# Note 2026-11-18 21:04

Book the passport appointment: Friday
```

The body is the dictated text unchanged, one paragraph per chunk. A note is written once and never edited by Chintu. `chintu://note/<note_id>` is the stable link to the note; the file path may move.

Notes saved before this change have no frontmatter. Readers must accept both forms.

## Compatibility rules

- Adding a frontmatter key is compatible. Renaming or removing one, changing `type`, or changing the file naming is a breaking change: record a decision and bump `okf_version` in new bundles.
- `services/gateway/tests/test_notes.py` parses a note with a full YAML parser and checks the keys above. Keep it in step with this document.
