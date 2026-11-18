# WardrobeShare — ten-slide presentation plan (24 Nov 2026)

1. **Project / problem** — Sharing clothes, planning OOTDs and tracking borrowing with friends.
2. **Functional overview** — Login, wardrobe, OOTD Gallery/Editor, Friends/Closet, Borrowing, Activity.
3. **Architecture** — Use `docs/diagrams/architecture.png`: one monolith, PostgreSQL, MongoDB, local media & AI.
4. **SQL ERD** — Use `docs/diagrams/sql_erd.png`; 9 relational tables and main FK paths.
5. **NoSQL collections** — Use `docs/diagrams/nosql_schema.png`; why flexible OOTD coordinates and logs belong in Mongo.
6. **CRUD demo** — Real screenshots of adding/editing/deleting garments, reminders and user notes (no fake mocks).
7. **Friends & privacy** — Show an allowed mixed OOTD and one hidden OOTD with an unauthorized clothing owner.
8. **SQL transaction & rollback** — Real before → failure after UPDATE+INSERT → after screenshots (no partial commit).
9. **Hybrid synchronization + CI** — Outbox queued/retry then Mongo event delivered; PR checks, deploy gate and image rollback.
10. **Limitations, conclusion & references** — No external runtime API, self-hosted local AI, eventual cross-DB consistency, demo scope, links to source/repo and class requirements.

Only use verified screenshots after executing real-DB gate; this outline is not a finished proof slide deck.
