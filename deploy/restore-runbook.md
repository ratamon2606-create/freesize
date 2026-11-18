# WardrobeShare restore drill (manual, maintenance window required)

**DO NOT restore over a live production instance without a verified off-host
copy, explicit owner approval and downtime.** Rollback scripts change only
application images, **not SQL/Mongo/upload files**. The database dumps and
uploads archives are created at separate points in time and therefore are
not a globally atomic cross-database snapshot.

1. Verify the SHA256SUMS file against the exact three matching backup files.
2. Build a **separate disposable restore environment** (distinct Docker project,
   fresh named volumes, no public HTTP ports) to test recovery first.
3. Restore PostgreSQL with `gunzip -c postgres-*.sql.gz | psql ...` and Mongo
   with `mongorestore --gzip --archive=<FILE> --drop` using a temporary
   authenticated restore Mongo container. Restore `uploads-*.tar.gz` into the
   disposable web's `/app/data` volume.
4. Verify SQL row counts, Mongo documents, and a sample uploaded image.
5. For a real incident, stop writes and coordinate a maintenance window;
   document recovery point and whether any recent operations will be lost.
6. Keep verified copies off the VPS on storage you control, encrypted and
   access-controlled. Do not put backup data in GitHub.

**This repository does not include an automatic destructive restore command.**
A planned restore should be reviewed by an operator and tested on spare volumes.
