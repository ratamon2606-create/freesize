# Submission screenshots — capture from the LIVE HYBRID APP

**Important:** Screenshots from the SQLite/JSON preview do **not** prove the required PostgreSQL/MongoDB operation. Use `docker compose` with PostgreSQL + MongoDB. Never fabricate a before/after screenshot or submit example data as proof of a successful transaction.

## Required screenshot checklist (save numbered PNG files here)

### SQL — PostgreSQL (user stories adapted from professor's e-commerce examples)
1. `01-register.png` — register account via UI and show `SELECT id, username, email FROM users;`
2. `02-wardrobe-create.png` — add clothing + SQL `clothing_items` row; image stored in self-hosted `uploaded_media` volume
3. `03-wardrobe-update-delete.png` — edit clothing details and soft-delete via UI; SQL row before and after
4. `04-friends.png` — friend request, accept and unfriend; SQL `friendships` row changes
5. `05-borrow-lifecycle.png` — borrow request, approval/handover and return, showing SQL joins of `borrow_requests`, `loans`, `clothing_items`

### NoSQL — MongoDB
6. `06-ootd-create-update-delete.png` — OOTD Gallery/Studio, SQL `outfit_slots` + Mongo `outfit_compositions`
7. `07-notification-crud.png` — create personal reminder / read / edit read flag / delete; inspect Mongo `notifications`
8. `08-activity-crud.png` — create/edit/delete user's journal entry; Mongo `activity_logs` (system events are immutable)
9. `09-borrow-log-crud.png` — create/edit/delete personal note; Mongo `borrow_status_logs` (system audit records are immutable)
10. `10-private-ootd.png` — May outfit with a piece from an unaccepted third friend must not be listed for viewer

### Transaction and rollback evidence (mandatory)
11. `11-transaction-before.png` — SQL state and loans before a deliberately failed transaction
12. `12-transaction-failed.png` — server/test output showing forced exception **after SQL UPDATE+INSERT**, not merely a form validation failure
13. `13-transaction-after.png` — state remains unchanged; no loan row present; SQL rollback shown
14. `14-transaction-success.png` — approved/handover/returned lifecycle and matching Mongo events
15. `15-outbox-retry.png` — Mongo temporarily fails; event remains queued in SQL, then marked processed after retry

The real PostgreSQL + MongoDB integration test writes `evidence/rollback-postgres.json` and `evidence/nosql-integration.json`; review them and take screenshots during the actual test as well. CI publishes those as a downloadable artifact. A JSON test proof is **not** a substitute for the professor's screenshot requirement.

### Diagrams (rendered versions already supplied in `docs/diagrams/`)
16. `16-architecture.png`, `17-sql-erd.png`, `18-nosql-schema.png` — copy the matching rendered diagram PNGs.

## Quick database inspection commands

```bash
docker compose exec postgres psql -U wardrobe -d wardrobeshare -c "SELECT id,username,email FROM users LIMIT 10;"
docker compose exec postgres psql -U wardrobe -d wardrobeshare -c "SELECT r.id,r.status,l.status AS loan_status,c.name,c.state,c.current_holder_id FROM borrow_requests r LEFT JOIN loans l ON l.request_id=r.id JOIN clothing_items c ON c.id=r.item_id;"
docker compose exec mongodb mongosh wardrobeshare --eval 'db.outfit_compositions.find().limit(5).pretty()'
docker compose exec mongodb mongosh wardrobeshare --eval 'db.notifications.find().limit(5).pretty()'
docker compose exec mongodb mongosh wardrobeshare --eval 'db.activity_logs.find().limit(5).pretty()'
docker compose exec mongodb mongosh wardrobeshare --eval 'db.borrow_status_logs.find().limit(5).pretty()'
```

Do not paste real email addresses or users' personal images in a public GitHub repository. Use demo/fictional accounts for screenshots and blur personal data.
