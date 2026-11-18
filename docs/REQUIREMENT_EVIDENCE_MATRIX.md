# Original DB Project Requirements — evidence matrix (18 Nov 2026)

This matrix corresponds to the attached **Project Requirements Specification: Hybrid Database Web Application**. The professor's shopping example is **illustrative**; WardrobeShare implements analogous borrowing/wardrobe/OOTD workflows.

| Requirement | Implementation | Proof / current status |
|---|---|---|
| Full-stack browser app | `static/` UI + `app.py` FastAPI | Preview 12/12 passed; real browser screenshot still required |
| SQL >3 connected tables | PostgreSQL with **9 tables**, PKs and FKs (`app.py` SQL_SCHEMA) | ERD source + PNG available. Real PostgreSQL must be verified by prepush gate |
| NoSQL >3 collections | MongoDB `outfit_compositions`, `notifications`, `activity_logs`, `borrow_status_logs` | NoSQL diagram and actual-DB test, still must run |
| No External APIs or Microservices | Single FastAPI service, *in-process* `rembg`; no CDN; no external runtime APIs | Code/compose static inspection. Offline pretrained `u2netp.onnx` model needed for cutout; one-time operator provisioning is separate from runtime API |
| Self-hosted SQL & NoSQL | Docker Compose `postgres:16` and `mongo:7`; no hosted database connections | Actual `docker compose ps` + database console screenshots required |
| Local images | `/app/data/uploads` volume, private `/media` route | Upload via UI and show local volume screenshot |
| CRUD for every business SQL entity | Users: register/read/edit/close; Clothing: create/list/edit/soft-delete; Friendships: request/list/accept/remove; Outfits: create/list/update/delete; Borrow requests: create/read/status update/delete concluded; Loans: create on handover/read in history/status update on return/delete with concluded history | UI route/API coverage + 12 preview tests; capture all screenshots. **Technical tables** (`sessions`, `clothing_upload_requests`, `outbox_events`) are not independently editable by arbitrary end users. Literal “every entity” still needs professor confirmation. |
| CRUD across 4 Mongo collections | Outfit composition: OOTD Studio/Gallery; Notifications: personal reminder UI; Activity: personal journal UI; Borrow status logs: personal note UI | API/UI routes added; system audit events deliberately immutable to preserve evidence. Real Mongo CRUD proof still pending |
| Atomic multi-operation SQL transaction & explicit rollback | SQL transaction (`Tx`) with commit/rollback, row locks & unique partial constraints; forced failure after SQL UPDATE and INSERT | SQLite regression passed; true PostgreSQL test script writes JSON evidence if successfully executed. Screenshots before/failure/after still required |
| SQL→Mongo multi-step integration | SQL outbox stored in same transaction, Mongo upsert/notification/event/log delivery with retry and idempotent IDs | Genuine Mongo integration/retry test scripted; **eventual consistency**, not full distributed ACID. Discuss this architectural tradeoff in presentation |
| GitHub public/instructor-accessible source + README | Git-ready source and README; Actions prepared | **Not pushed**, by user instruction; create repo only after gate |
| System architecture, SQL ERD, NoSQL schema | `docs/diagrams/*.png`, SVG, DOT; Mermaid docs | Rendered diagrams ready; screenshots copy into `docs/screenshots/` |
| Dedicated screenshots of all functionality and transaction proof | `docs/screenshots/README.md` checklist + test-produced JSON evidence | **NOT COMPLETE:** real screenshots must be captured on a running hybrid instance |
| Due 23 Nov; 24 Nov presentation max 10 pages | `docs/PRESENTATION_OUTLINE.md` | Outline ready; actual final slide deck and captured evidence still required |

## Mandatory gates before first GitHub Push

- [ ] `bash scripts/prepush_gate.sh` exits 0, using real PostgreSQL + MongoDB and the upgraded dependencies
- [ ] Inspect output `docs/screenshots/evidence/rollback-postgres.json` and `nosql-integration.json`
- [ ] Open `localhost:3000` on Ubuntu; manually check all pages, phone/mobile width, actual upload, OOTD, Friends, loan lifecycle and all CRUD controls
- [ ] Confirm `u2netp.onnx` local AI removal works (or clearly disclose optional feature gap if not)
- [ ] Capture genuine UI + DB screenshots listed in `docs/screenshots/README.md`; never copy mockups as transaction evidence
- [ ] Verify backup + restore procedure on disposable database
- [ ] Confirm instructor agrees that internal technical tables and immutable security audit records do not require unsafe arbitrary CRUD UI, and that transactional outbox meets cross-database consistency requirement
- [ ] Ensure Git excludes secrets, uploads, real account data and model weights

**The project is not certified “100% compliant” while any required gate is unchecked.**
