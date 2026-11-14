# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting: the "Security" tab of the repository, then "Report a vulnerability". Include what you found, how to reproduce it and the version (`leadcommons version`).

You can expect an acknowledgement within a few days and a fix or a plan within 30 days. Credit is given unless you prefer otherwise.

## Scope

In scope: the web app, CLI, Docker images, deployment files and installers in this repository. Out of scope: your own server configuration and third-party services.

## Hardening tips for self-hosters

- Use server mode with `LEADCOMMONS_PASSWORD_HASH` (never a plain password) and a long random `LEADCOMMONS_SECRET_KEY`.
- Serve only over HTTPS and keep `LEADCOMMONS_COOKIE_SECURE=1`.
- Keep `.env` private (`chmod 600`) and update images regularly.
- Back up the Postgres volume.
