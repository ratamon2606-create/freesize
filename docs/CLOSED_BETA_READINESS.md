# Closed-beta readiness checklist (not a claim of live deployment)

| Area | Implemented in code? | Verified on a public VPS? |
|---|---|---|
| Invite-only signup; no seeded users | Yes | Not yet |
| Server-side friend privacy and ownership | Existing tests | Not yet |
| Upload image validation and single-process throttling | Yes | Not yet |
| Private SQL/Mongo, durable Docker volumes | Production compose | Not yet |
| In-process rembg, mounted offline model | Prior laptop test | Not yet |
| HTTPS via Caddy and secure session cookies | Configured | Not yet |
| PR required checks + GHCR release | Configured | Requires repository setup |
| Internal + public health checks & auto rollback | Scripted | Not yet exercised on VPS |
| Daily SQL/Mongo/photos backup + restore drill | Scripted / documented | Restore not yet verified |
| Existing integration gate on user's Ubuntu laptop | Earlier baseline passed | Re-run after this revision |

**Known gaps:** no verified password reset, no email ownership verification, no distributed rate limiting,
no zero-downtime rolling deployment, no automatic DB rollback. This is a closed beta for
5–10 invited friends, not a generally secure public SaaS launch. The user must supply a
VPS, domain, SSH and GHCR credentials; none are present in this repository.
