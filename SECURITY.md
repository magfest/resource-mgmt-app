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

- Content Security Policy with nonces (no inline handlers)
- CSRF protection via Flask-WTF on all forms
- Dependency scanning via pip-audit (pre-commit and CI)
- Dependabot alerts enabled
- Role-based access control with per-route permission checks
- Session timeout and sliding window expiration

See the [Roadmap](ROADMAP.md#security--infrastructure) for planned security improvements.

## Supported Versions

This project is under active development with a single production branch. Security fixes are applied to the latest version only.
