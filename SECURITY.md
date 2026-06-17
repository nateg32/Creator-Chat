# Security Policy

## Supported Versions

Security fixes are handled on the `main` branch. If tagged releases are added later, this document will be updated with supported release lines.

## Reporting A Vulnerability

Please do not open a public issue with exploit details, credentials, private URLs, database dumps, or sensitive logs.

Use GitHub private vulnerability reporting when available. If it is not enabled, open a minimal public issue asking for a secure contact path without including technical exploit details.

Helpful reports include:

- A short description of the issue.
- Affected endpoint, component, or workflow.
- Reproduction steps using fake data.
- Potential impact.
- Suggested fix, if known.

## Secrets

Never commit `.env` files, provider API keys, database URLs, Render/Vercel tokens, cookies, session tokens, or private keys.

If a secret is committed, rotate it immediately. Removing it from the latest commit is not enough if it exists in Git history.

## Deployment Notes

- Use a strong `JWT_SECRET_KEY`.
- Use HTTPS and `COOKIE_SECURE=true` in production.
- Restrict CORS to trusted frontend origins.
- Keep database credentials out of client-side code.
- Review scraped content and logs before sharing demo data.

## Responsible Disclosure

We aim to acknowledge security reports promptly and prioritize fixes based on severity and exploitability.
