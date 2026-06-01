# Agent Instructions

## Changelog Discipline

Always update `CHANGELOG.md` whenever you make a code or documentation change, including:

- bug fixes
- patches
- refactors that change behavior or project structure
- new MCP tools or features
- test additions or meaningful test changes
- documentation updates that affect setup, usage, or public behavior

Always group changelog entries by date. Each date has exactly one `## YYYY-MM-DD` heading, placed at the top of the file (newest date first). Under that date heading, add each logical change set as an `### Title` subsection. Never create a second heading for a date that already exists — if today's `## YYYY-MM-DD` is already present, add your `###` subsection under it (newest subsection first) instead of starting a new dated block. Keep entries concise and user-facing: describe what changed and why it matters, not every internal implementation detail.

If several related edits are part of one task, group them under one `###` subsection. If the task is purely exploratory and leaves no file changes, do not update the changelog.