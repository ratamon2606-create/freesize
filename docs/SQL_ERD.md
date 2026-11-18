# SQL ER Diagram

```mermaid
erDiagram
 USERS ||--o{ SESSIONS : has
 USERS ||--o{ CLOTHING_ITEMS : owns
 USERS ||--o{ CLOTHING_ITEMS : holds
 USERS ||--o{ FRIENDSHIPS : requests
 USERS ||--o{ FRIENDSHIPS : receives
 USERS ||--o{ OUTFIT_SLOTS : saves
 USERS ||--o{ BORROW_REQUESTS : lender
 USERS ||--o{ BORROW_REQUESTS : borrower
 CLOTHING_ITEMS ||--o{ BORROW_REQUESTS : requested
 BORROW_REQUESTS ||--o| LOANS : becomes
 CLOTHING_ITEMS ||--o{ LOANS : transferred
 USERS ||--o{ LOANS : lender
 USERS ||--o{ LOANS : borrower
 USERS { string id PK string username UK string email UK string password_hash string display_name }
 SESSIONS { string id_hash PK string user_id FK string expires_at }
 CLOTHING_ITEMS { string id PK string owner_id FK string current_holder_id FK string name string category string image_path string state }
 FRIENDSHIPS { string id PK string requester_id FK string addressee_id FK string status }
 OUTFIT_SLOTS { string id PK string owner_id FK string title }
 BORROW_REQUESTS { string id PK string item_id FK string owner_id FK string borrower_id FK string status }
 LOANS { string id PK string request_id FK string item_id FK string lender_id FK string borrower_id FK string status }
 OUTBOX_EVENTS { string id PK string event_type string payload_json string processed_at }
```

Primary/foreign-key constraints are created by `SQL_SCHEMA` inside `app.py`. Application-level `FOR UPDATE` locking in PostgreSQL and transactional mutations prevent conflicting handovers.


## Upload retry protection (v1.2)

New SQL table `clothing_upload_requests`:

- `user_id` (FK -> `users.id`)
- `request_token` (client-generated token per upload form)
- `clothing_id` (FK -> `clothing_items.id`)
- `created_at`
- composite PRIMARY KEY (`user_id`, `request_token`)

A repeated POST for the same user and token returns the prior item rather than creating another item or activity event. The first row is written atomically with the clothing item and its outbox event. Existing databases are migrated non-destructively with `CREATE TABLE IF NOT EXISTS`.

## Rendered diagram

![SQL ERD](diagrams/sql_erd.png)

PostgreSQL enforces one reserved or borrowed request per item and one active loan per item using **partial unique indexes**; otherwise `FOR UPDATE` locks protect borrowing transitions. `sessions`, upload deduplication requests and outbox rows are internal system entities.
