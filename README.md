<div align="center">

# Freesize

**Your wardrobe, your friends, endless outfit combinations.**

A social wardrobe web application for organizing real clothing, building outfits with friends, and tracking shared pieces — all in one place.

**Project status:** Closed-beta candidate · Self-hosted · Not yet publicly deployed

</div>

## About the project

Freesize helps friends turn their individual closets into a shared creative space. Upload and organize your clothes, automatically remove image backgrounds using an **offline local AI model**, arrange garment cutouts on a free-form OOTD canvas, save your looks, explore friends' wardrobes, and manage borrowing with a clear ownership trail.

The interface uses a warm **vintage textile / brown gingham** design with real clothing photos rather than vector garments.

## Features

| Area | What you can do |
| --- | --- |
| Account & profile | Register, sign in/out, edit your profile and avatar; invite-only registration for closed beta |
| My Wardrobe | Upload or take clothing photos, remove backgrounds locally, manage details, edit/delete items, track current holder |
| OOTD Gallery & Studio | Build outfits on a drag-and-drop canvas, resize and layer clothes, save/edit/delete slots, inspect item ownership |
| Friends | Search usernames, send/accept/reject requests, manage friends, explore their permitted wardrobes |
| Borrowing | Request, approve/reject, hand over, return, confirm receipt, and view history |
| Notifications & activity | Receive and manage notifications; review personal activity and borrowing notes |

### Privacy by design

A friend's saved OOTD is visible **only if the viewer is permitted to access every clothing item in it**. If even one garment belongs to someone outside the viewer's accepted friend circle, the **whole outfit is hidden server-side**, including its preview. Using a friend's garment in a digital OOTD **does not** change who owns or physically holds it.

## Technology

| Layer | Stack |
| --- | --- |
| Frontend | HTML, CSS, vanilla JavaScript |
| Backend | Python, FastAPI |
| SQL | Self-hosted PostgreSQL (9 tables) |
| NoSQL | Self-hosted MongoDB (4 collections) |
| Background removal | `rembg` / U²-NetP ONNX model, in-process, offline |
| Storage | Local server filesystem and persistent Docker volumes |
| Infrastructure | Docker Compose, Caddy (HTTPS), GitHub Actions |

**Database design:** SQL stores relational ownership, friends, loan state and atomic transactions. MongoDB stores flexible outfit compositions, notifications and logs. A transactional **outbox** carries changes from SQL to MongoDB with retry; this is *eventual consistency*, not a cross-database ACID transaction.

![System architecture](docs/diagrams/architecture.png)

Other design references: [SQL ERD](docs/diagrams/sql_erd.png) · [NoSQL Schema](docs/diagrams/nosql_schema.png).

## Run locally

Requirements: Docker and Docker Compose on Linux (Ubuntu recommended).

```bash
cp .env.example .env
# Edit .env and replace the example POSTGRES_PASSWORD with a unique local password.
# SEED_DEMO=1 enables sample accounts/items for a disposable local demo.
# SEED_DEMO=0 on a fresh database keeps the app free of demo users and items.
docker compose up -d --build
docker compose ps
curl http://localhost:3000/api/ready
```

Open **http://localhost:3000**. By default the web service is bound to localhost; this is **not a public deployment**. For a real environment, see [Production Release Checklist](docs/PRODUCTION_RELEASE_CHECKLIST_TH.md).

### Offline background removal

The model weights are not stored in Git. Place the verified `u2netp.onnx` file in `models/u2netp.onnx` on the host. The model is loaded inside the web backend; the app does not call a third-party background-removal API.

For production, configure `REQUIRE_BACKGROUND_AI=1` and verify the readiness endpoint and actual image cutout before launch.

## Tests and CI/CD

Before opening a pull request, run:

```bash
bash scripts/prepush_gate.sh
```

The pre-push gate checks source requirements, preview tests, real PostgreSQL + MongoDB integration, rollback behavior, and Docker image build. **Always review the output**: a successful earlier run is not proof that later changes passed.

GitHub Actions provides PR checks for API, browser UI, hybrid databases, Docker build and dependency security. Production delivery is **disabled by default** until a server, HTTPS domain, secrets and deployment approval have been configured. Deployment uses immutable image digests, health checks and automatic **web image rollback**; database backups/restores are a separate process.

See [Collaboration Guide](docs/CONTRIBUTING_TH.md) and [Production Release Checklist](docs/PRODUCTION_RELEASE_CHECKLIST_TH.md).

## Project documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API reference](docs/API_QUICK_REFERENCE.md)
- [SQL ERD explanation](docs/SQL_ERD.md) and [MongoDB schema](docs/NOSQL_SCHEMA.md)
- [Requirement evidence matrix](docs/REQUIREMENT_EVIDENCE_MATRIX.md)
- [Screenshots checklist](docs/screenshots/README.md)
- [Presentation outline](docs/PRESENTATION_OUTLINE.md)

## Contributing

We welcome contributions from the project team, especially UI and accessibility improvements. Create a branch such as `ui/wardrobe-layout`, open a pull request against `main`, include screenshots of visual changes, and wait for required checks and review before merge. **Do not push directly to `main`.**

Please never commit `.env`, private uploads, ONNX weights, real-user screenshots, database files or credentials. See [the Thai contributing guide](docs/CONTRIBUTING_TH.md).

## Notes

Freesize is the **public-facing project name**. Some internal folders, Docker services and code identifiers still use the earlier name `wardrobeshare` to preserve existing working configurations; renaming internals should be a separate, tested pull request.

This is a student hybrid-database project and a closed-beta candidate. Public hosting, end-to-end production rollback/restore testing, and final instructor submission evidence are separate release tasks. No public URL is claimed yet.

**License:** No open-source license has been selected. Permission to contribute as a collaborator does not require making the repository open-source.
