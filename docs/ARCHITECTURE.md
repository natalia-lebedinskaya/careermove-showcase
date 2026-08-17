# Architecture

## Components

| Component | Responsibility |
| --- | --- |
| `web/` | React 19 PWA, authentication flow, profile editing, vacancy review, and application workspace |
| `api/main.py` | FastAPI routes, authorization, search orchestration, ranking endpoints, drafts, and notifications |
| `api/database.py` | SQLite/PostgreSQL adapter and transaction boundary |
| `app/live_jobs.py` | Source adapters, normalization, freshness checks, deduplication, scoring, and cache handling |
| `app/public_release.py` | Password hashing, sessions, user-scoped settings, and recovery helpers |
| `api/worker.py` | Bounded background search entrypoint |

## Search lifecycle

1. The client starts a search for an authenticated user.
2. The API loads that user's candidate profiles and source configuration.
3. Source adapters fetch public records within bounded time budgets.
4. Records are normalized to a common shape and matched by provider ID, URL, and cross-source fingerprint.
5. Existing cards are revalidated; new and changed records are upserted without losing application status.
6. Hard constraints remove unsafe or impossible matches.
7. The deterministic ranker scores each candidate independently.
8. Optional model providers may rerank a reduced shortlist, but cannot invent source URLs.
9. The UI receives current cards plus diagnostics for partial provider failures.

## Data boundary

Authentication, profiles, resumes, vacancies, settings, and application events carry a `user_id`. Production deployments should use PostgreSQL. SQLite is intended for an isolated local demo.

Secrets are read from the environment. The repository excludes local databases, resumes, exports, spreadsheets, deployment state, and `.env` files. Optional AI requests receive role and skill context without names, citizenship, or direct contact details.

## Reliability choices

- Feed failure is additive, not destructive: a failed provider does not replace cached results with an empty set.
- Search work is split into bounded batches for serverless runtimes.
- Scheduled digests use slot-based deduplication so independent schedulers cannot send the same digest twice.
- Sessions use opaque revocable tokens; passwords use salted PBKDF2 and legacy hashes upgrade after a valid login.
- The client distinguishes authentication loss, API health, and optional AI-provider health.
