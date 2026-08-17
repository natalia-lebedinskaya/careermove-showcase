# Five-minute demo

## Scenario

1. Register a local showcase account.
2. Open **Profiles** and create two candidates with the QA and Support presets.
3. Show how role, geography, language, salary, exclusions, and skill levels differ per candidate.
4. Start one vacancy search and point out source diagnostics and graceful partial failure.
5. Open the same vacancy for both profiles to compare independent scores and explanations.
6. Add a card to favorites, then open its three cover-letter variants.
7. Move the card to **In progress** and show the application timeline.
8. Switch theme and viewport width to demonstrate persisted preferences and responsive PWA navigation.

## Talking points

- The core ranking works without paid AI APIs.
- Optional models refine a shortlist; they do not collect vacancies or invent links.
- Source freshness and state preservation are more important than a large unverified result count.
- The workflow prepares high-volume applications while keeping the final decision and submission manual.
- The showcase data is fictional and isolated from the production deployment.

## Suggested review paths

- Search adapters and scoring: `app/live_jobs.py`
- Authentication and session lifecycle: `app/public_release.py`
- API orchestration: `api/main.py`
- Product UI: `web/src/App.tsx`
- Regression checks: `tests/test_runtime_regressions.py`
