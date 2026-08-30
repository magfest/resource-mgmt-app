# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly. **Do not open a public GitHub issue.**

Instead, email **code@magfest.org** with:

- A description of the vulnerability
- Steps to reproduce it
- The potential impact
- Any suggested fix (if you have one)

We will acknowledge your report within 72 hours and work with you to understand and address the issue. We'll coordinate disclosure timing with you.

## Scope

This policy covers the MAGFest Budget System codebase and its deployment infrastructure. If you find an issue with a third-party dependency, please report it to us as well so we can assess the impact and update accordingly.

## What We Consider In Scope

- Authentication or authorization bypasses
- SQL injection, XSS, CSRF, or other OWASP Top 10 vulnerabilities
- Exposure of sensitive data (PII, credentials, internal pricing)
- Session management issues
- Privilege escalation between roles

## What We Have in Place

| Control | Where it is configured |
| --- | --- |
| Content Security Policy with per-request nonces; no inline event handlers | `app/__init__.py`, documented in [docs/security.md](docs/security.md) |
| CSRF protection via Flask-WTF on all forms | `CSRFProtect` in `app/__init__.py` |
| Dependency scanning via pip-audit, in pre-commit and on pushes to master and pull requests | `.pre-commit-config.yaml`, `.github/workflows/security.yml` |
| Static analysis via bandit on pushes to master and pull requests | `.github/workflows/security.yml` |
| Dependabot weekly pip version updates | `.github/dependabot.yml` |
| Role-based access control with per-route permission checks | [docs/permissions.md](docs/permissions.md) |
| Session timeout with sliding expiration, 30 minutes in production and 60 in development | `SESSION_TIMEOUT_MINUTES` in `app/__init__.py` |
| Security audit logging of logins, logouts, 403s, and impersonation | [docs/security.md](docs/security.md) |

The [roadmap](ROADMAP.md) records security work. As of August 2026 its security
entries are completed items, not planned ones.

## Supported Versions

This project is under active development with a single production branch. Security fixes are applied to the latest version only.
