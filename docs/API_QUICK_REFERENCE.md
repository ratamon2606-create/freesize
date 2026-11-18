# API Quick Reference (requires authenticated cookie except auth and health)

**Account**: `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`, `PATCH /api/me`, `POST /api/me/avatar`

**Wardrobe**: `GET /api/wardrobe`, `POST /api/wardrobe` (multipart image and metadata), `PATCH /api/wardrobe/{id}`, `DELETE /api/wardrobe/{id}`. Supported fields: `name`, `category`, `color`, `details`; upload includes `file`, optional `request_token` for safe retry.

**Friends**: `GET /api/users/search?q=`, `GET /api/friends`, `POST /api/friends` (username), `PATCH /api/friends/{id}` (`accept`, `reject`, `cancel`), `DELETE /api/friends/{id}`, `GET /api/friends/{user_id}/closet`.

**OOTD**: `GET /api/ootds`, `POST /api/ootds`, `PATCH /api/ootds/{id}`, `DELETE /api/ootds/{id}`. Outfit item document: `{ item_id, x, y, w, z }` with normalized canvas coordinates. Friend's outfits with an inaccessible clothing owner are not returned.

**Borrowing**: `GET /api/borrowing`, `POST /api/borrowing` (`item_id`), `PATCH /api/borrowing/{id}` with action in: `approve`, `reject`, `cancel`, `handover`, `request_return`, `confirm_return`.

**Notifications / Activity**: `GET /api/notifications`, `PATCH /api/notifications/{id}`, `DELETE /api/notifications/{id}`, `GET /api/activity`, `POST /api/activity`, `PATCH /api/activity/{id}`, `DELETE /api/activity/{id}`, `PATCH /api/borrow-logs/{id}`.

**Service checks**: `GET /api/health` (shallow environment information), `GET /api/ready` (real SQL/NoSQL and in-process offline AI model availability if REQUIRE_BACKGROUND_AI=1; non-200 when unhealthy).

All mutating browser calls in HTTPS production require a same-origin `Origin` or `Referer` matching `PUBLIC_ORIGIN`. These are not external third-party APIs: they are the app's own internal endpoints.

## Additional strict CRUD APIs (November 2026)

- `PATCH /api/me` — editable name, username, email, bio and optionally password (`current_password` plus `new_password`).
- `DELETE /api/me` — account closure, revokes sessions, pseudonymizes SQL user record and blocks closure while loans/requests are active.
- `POST /api/notifications` — create a personal reminder (Mongo); `GET /api/notifications`, `PATCH /api/notifications/{id}` and `DELETE` complete CRUD.
- `POST /api/borrow-logs` — add a personal borrowing note (Mongo); `GET /api/activity`, `PATCH /api/borrow-logs/{id}`, `DELETE /api/borrow-logs/{id}` complete personal-note CRUD. System audit logs cannot be deleted.
- `DELETE /api/borrowing/{id}` — delete a terminal (cancelled/rejected/returned) SQL borrowing request and its completed loan inside a single transaction; rejects active loans.
- `GET /api/ready` — dependency readiness; if `REQUIRE_BACKGROUND_AI=1`, fail if offline `u2netp.onnx` model absent.
