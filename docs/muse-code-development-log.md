# StorySpine — Muse Code Development Log (Meta Hackathon Submission)

Date: 2026-11-04 / For: Meta Global AI Developer Hackathon
Track: Indie Developer Track / Theme: Build for Your World

This document describes how StorySpine was built with **Muse Code
(Meta's agentic coding tool)**, grounded in git history and measured
repository facts. It supports the development-assistance part of the
form item "Describe your use of Meta products".

## 1. Product snapshot

- **StorySpine** — *Give your story a spine.* An AI novel studio linking
  planning, drafting, and canon review for serialized fiction.
- Core loop per chapter: draft → extract → verify → fix → publish,
  guarded by "author-approved memory" across chapters.
- Stack: FastAPI server + build-free static frontend (HTML/CSS/JS) +
  SQLite, bilingual UI (ko-KR/en-US) with KR/US writing markets.
- Runtime AI: Meta Model API, `muse-spark-1.3` by default with an
  optional `muse-wonder-1.0` preset. Every AI call leaves through one
  module, `server/app/meta_client.py` (sole AI exit).

## 2. How we worked: human direction, agentic implementation

- **Human (author)**: product planning, pipeline/state-machine/memory
  design, reviewing every step, judging evaluation cases, final
  approval, commit, and push.
- **Muse Code**: server/frontend implementation per the design, pytest
  authoring and runs, prompt/schema writing, bug diagnosis and fixes,
  UI iteration, doc drafts.
- **Unit of work**: short instruct → implement → test → review → commit
  cycles per feature; commit messages mirror the instruction units.
- **Verification principle**: mock/fixture pytest (375 tests) plus
  instrumented real-model runs; every runtime AI claim is demonstrated,
  not asserted.

## 3. Development timeline (git history, 10/28–11/4, 63 commits)

| Date | Stage | Work (representative commits) |
|---|---|---|
| 10/28 | Phases 0–8, MVP done | Scaffolding → memory store → planning → drafting → verify/memory → quality/approval → eval records → README/reproduction. Full plan-to-approve pipeline plus a 3-chapter reproduction (3/3 COMMITTED) in one day (`c57e05f`–`aed3444`) |
| 10/29 | Frontend switch | React removed, pure HTML/CSS/JS static frontend (`a2c5fa9`) |
| 10/30 | Ideas + brand | Ideas-chat P0, StorySpine brand lock (`6c87bbb`, `3eadd68`) |
| 10/31 | Design screen | Legacy frontend removed, canonical UI, model picker, story-design screen (`a21aedb`–`af5d2a2`) |
| 11/1 | Settings + auth | Scoped-canon node settings CRUD/inheritance, tree improvements, author auth and work ownership (`638bb0d`–`9140ed2`) |
| 11/2 | Writing + publishing | Episode writing studio, manuscript-doc API, writing↔review transitions, 3-state (WRITING/IN_REVIEW/PUBLISHING) switch (`f5b5aa2`–`a47b104`) |
| 11/3 | Memory depth + polish | Fact model, deterministic checks, episode isolation, eval set, review UX, AI-fix flow, publishing simplification (`0c09daa`–`dca8077`) |
| 11/4 | i18n, hardening, submission | Full EN localization (6 screens, prompts, markets), 2-model presets, novel-delete scope fixes, start-gate relaxation, review UX (skip-as-info, stale-saved display), per-author token budget, `MAX_TOKENS_PER_CALL` wiring, README rewrite, sample story, demo videos (`381cdf1`–`0176c09`) |

## 4. Work by area (measured 11/4)

| Area | Delivered | Location |
|---|---|---|
| Server API | Works, designs, episodes, manuscripts, memory, publishing REST APIs, state machine, budget/retry/quote checks | `server/app/` 28 modules, `routes/` 12, `schemas/` 8 |
| Prompts | Locale-aware prompt set for planning, drafting, extraction, verification, quality, chats, blurb | `server/app/prompts/` 16 files |
| Frontend | 6 screens (Ideas, Story Design, Writing, Review, Publishing, Entities), router, resizers, AI log panel | `frontend/` 17 JS modules, ~7,000 lines |
| Tests | API, state machine, memory, quotes, consistency, publishing, i18n contracts | `server/tests/` 31 files, 375 tests |
| Evaluation | Smoke/injection/reproduction measurements plus deterministic paired suite | `evals/*.md`, `evals/cases.json` + `test_eval_quality.py` |
| Bug fixes | Missing commits, NaN errors, handoff conflicts, cache invalidation, delete FK/orphans, duplicate-start 409, extraction truncation | git history (see timeline) |
| Docs | README, reproduction, brand, eval notes, submission drafts | `README.md`, `docs/` |

Measured size: server Python ~17,400 lines (tests included), frontend JS ~7,000 lines.

## 5. Meta usage mapping (form correspondence)

Runtime and development assistance are separate.

- **Runtime (called while the app runs)**: Meta Model API,
  `muse-spark-1.3` (default) for extraction, continuity review,
  planning, and drafting. Evidence: `server/app/meta_client.py`,
  `server/app/prompts/`, `evals/결과_20261028.md`, in-app AI call log.
- **Development assistance (unrelated to runtime)**: Muse Code.
  Scope = §4 implementation, tests, and docs. Evidence = this log +
  63 commits in git history.
- **Not used**: image generation and partner technologies
  (Fireworks/Modal/Baseten) are not part of this submission; those
  form items are marked not-used.

## 6. Evaluation summary (small samples, tuning-grade)

Per `evals/결과_20261028.md` — tuning samples (n≤3), not general accuracy.

- Deterministic paired suite: 12/12 (`test_eval_quality.py`, no LLM).
- Reproduction pipeline: 3/3 chapters complete (COMMITTED), prior
  chapters' state reflected.
- Injection probes: cooldown violation 1/1, homonym kept split,
  normal chapter 0 false positives.
- Measured cost: 3 chapters, 21 calls, in 49,866 + out 119,041
  tokens, 537 s wall clock (~56k tokens/~170 s per chapter).
- Limits: no held-out confirmation set, no recurrence rate, no
  currency cost, no study with working authors. Stated as limits in
  the submission.

## 7. Paste-ready paragraph for the form

Use as the development-assistance paragraph of "Describe your use of
Meta products", together with the runtime model description.

```text
During development, I used Muse Code, Meta's agentic coding tool, to implement
the application from my pipeline and memory-model design. It produced the
FastAPI server (28 modules), the static HTML/CSS/JS frontend (17 modules), a
locale-aware prompt set, and a pytest suite of 31 files with 375 tests, and it
diagnosed and fixed bugs such as FK/orphan deletes, duplicate-start conflicts,
and extraction truncation. The full iteration history is visible as 63 commits
from October 28 to November 4. This development assistance is separate from the
Meta model calls used by the application at runtime.
```

## 8. Evidence and reproduction

- Repository: `https://github.com/kimiyo/meta-hackathon-20261026` (verify public at submit time)
- Range: `8408601` (initial, 10/28) → `0176c09` (11/4), 63 commits
- Run/reproduce: README install, run, and reproduction steps as written
- Security: `.env` untracked and never committed, key confined to server memory, secrets scan performed
