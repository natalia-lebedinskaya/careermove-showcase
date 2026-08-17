# Security policy

## Supported version

The latest `main` branch is the supported showcase version.

## Reporting

Please do not publish credentials or personal data in an issue. Report a suspected vulnerability privately through the repository owner's GitHub profile.

## Local data

The local demo stores its SQLite database under `data/`, which is ignored by Git. Delete that directory to reset the showcase. Never commit `.env`, database, resume, spreadsheet, export, or deployment-state files.

## Deployment

Use environment variables or the hosting provider's secret store. Set a stable `CAREERMOVE_API_SECRET`, restrict `CORS_ORIGINS`, use PostgreSQL in production, and configure scheduled endpoints with `CRON_SECRET`.
