---
name: build-deck
description: Build a Recur outreach deck for a company. Use when the user says "build a deck for <company>"
  or asks for a Recur deck, mailer deck or first-touch deck for a company.
---
1. Extract the company name (and a domain or industry hint if the user gave one).
2. Run: `recur-deck "<name>" [--domain <d>] [--hint "<h>"] --non-interactive --out ./output`
   from the repo root (activate the venv first if `recur-deck` is not on PATH:
   `source .venv/bin/activate`). The run takes 3-6 minutes; let it finish.
3. Exit code 3 means the name is ambiguous: show the user the candidate domains printed by the command,
   ask which one, then re-run with --domain.
4. When it finishes, report the PDF and PPTX paths, the QA status, and any fallbacks listed in
   qa_report.json (typeset logos, thin market, low-confidence identity). Do not edit the deck by hand.

Exit codes: 0 pass · 1 pass with warnings or needs review (deck delivered; say what to check) ·
2 error (show the `error` and `failed_stage` from the printed report) · 3 ambiguous company name.
