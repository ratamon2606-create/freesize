## What changed?

- [ ] UI / CSS only
- [ ] Backend / API
- [ ] Database / migration
- [ ] Infrastructure / CI/CD

## Before merging

- [ ] I tested the feature in a browser (desktop + mobile if UI changed).
- [ ] I ran `bash scripts/prepush_gate.sh` locally if Docker available.
- [ ] GitHub CI is green (API, browser, hybrid DB, image build, audit).
- [ ] No `.env`, passwords, uploads, model weights, screenshots with private user data or databases committed.
- [ ] Existing users' data remains compatible (no destructive schema change).

## Screenshots / evidence

Add screenshots for UI changes, using sample or consented data only.

## Risks and rollback plan

What could break after deployment? Is rollback by image enough?
