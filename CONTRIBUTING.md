# Contributing To Creator Chat

Thanks for taking a look at Creator Chat. The project is intended to be useful, hackable, and safe to run as a self-hosted app.

## Getting Started

1. Fork the repository.
2. Create a branch from `main`.
3. Follow the setup instructions in `README.md`.
4. Keep secrets in local `.env` files only.
5. Open a pull request with a clear description of the change and how you tested it.

## Development Checks

Run focused checks for the area you changed:

```powershell
python -m pytest backend/tests
cd frontend/creator-chat
npm run lint
npm run build
```

For open-source packaging changes, also run:

```powershell
python -m pytest backend/tests/test_open_source_readiness.py
```

## Pull Request Guidelines

- Keep changes focused and explain the user-facing impact.
- Include tests for backend behavior when practical.
- Avoid unrelated formatting churn.
- Do not commit generated logs, local databases, `.env` files, API keys, screenshots with private data, or provider output containing sensitive content.
- For scraper changes, include graceful empty/error handling and platform-status messages.
- For AI behavior changes, prefer source-grounded outputs and avoid making the app impersonate creators as if replies were official statements.

## Responsible Content Handling

Creator Chat is built around public creator content and user-approved ingestion. Contributors should preserve that review-first workflow and avoid features that silently ingest private, paywalled, or unauthorized content.

## Code Of Conduct

By participating, you agree to follow `CODE_OF_CONDUCT.md`.
