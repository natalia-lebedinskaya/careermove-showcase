# Product decisions

## Problem

International remote job seekers often review the same vacancy across several boards, maintain separate materials for different candidates, and lose track of freshness or application status. CareerMove centralizes those steps without automating the final submission.

## Why deterministic ranking comes first

Language level, geography, contract restrictions, role seniority, and required skills are explicit constraints. They are evaluated locally for predictable behavior and offline fallback. AI is optional and limited to reranking and draft assistance.

## Why the system stores source history

A job card is not a static recommendation. It can be updated, removed, republished, or duplicated by another board. The data model keeps source identity, first/last-seen timestamps, content hashes, and user workflow state so refreshes can be useful without being destructive.

## Why applications are not sent automatically

Job boards and employers have different terms, questions, and identity checks. CareerMove creates editable drafts and direct source links, but a person verifies the vacancy and sends the application.

## Current limitations

- Public feeds can be incomplete, rate-limited, or temporarily unavailable.
- A high match score is a prioritization aid, not a hiring probability.
- Salary estimates are discussion anchors when a source does not publish compensation.
- Employer reputation, legal eligibility, and relocation support must be verified on the original posting.
- The showcase deliberately excludes production integrations and private user data.
