# Agent Instructions

## Changelog Discipline

Always update `CHANGELOG.md` whenever you make a code or documentation change, including:

- bug fixes
- patches
- refactors that change behavior or project structure
- new MCP tools or features
- test additions or meaningful test changes
- documentation updates that affect setup, usage, or public behavior

Add a new dated entry at the top of `CHANGELOG.md` for each logical change set. Keep entries concise and user-facing: describe what changed and why it matters, not every internal implementation detail.

If several related edits are part of one task, group them under one changelog entry. If the task is purely exploratory and leaves no file changes, do not update the changelog.