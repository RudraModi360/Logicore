---
title: Contributing
description: How to contribute documentation, code, and issues to Logicore.
---

# Contributing

Thanks for helping improve Logicore. This guide keeps contributions consistent and quick to review.

## Ways to contribute
- Fix or clarify docs
- Improve examples and code samples
- Report bugs with minimal repro steps
- Propose features with a short problem statement

## Workflow
1. Fork and create a branch: `git checkout -b docs/<topic>`
2. Run docs locally: `npm install` (first time) then `npm run dev`.
3. Keep changes small and scoped to one topic.
4. Add/update tests when touching code; run `pytest` if relevant.
5. Open a PR with a concise summary and checklist of what changed and how to validate.

## Documentation style
- Prefer plain, neutral language over marketing.
- Keep headings shallow: one H1 per page, then H2/H3 for structure.
- Include language hints on code fences (` ```python `, ` ```bash `, etc.).
- Add short intros before long code blocks to set context.
- Use callouts for warnings, tips, and notes.

## Commit message format
Use imperative mood, e.g. `docs: clarify provider table`.

## Definition of done
- Mintlify build passes: `npm run build`.
- No broken links in navigation.
- Content is readable on mobile (line length and code wrapping).
- PR description lists manual verification steps.

## Code of conduct
Be respectful and constructive. Disagreements are fine—keep them fact-based and kind.
