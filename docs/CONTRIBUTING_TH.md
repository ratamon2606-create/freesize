# คู่มือก่อน Push และแบ่งงาน UI — WardrobeShare

## ขั้นที่ 1: ตรวจเครื่องและโปรเจกต์ (ยังไม่ Push)

1. แตก ZIP เวอร์ชันล่าสุด ไม่ต้องแทนที่ฐานข้อมูล Docker เดิม
2. เปิด Terminal ที่โฟลเดอร์ `wardrobeshare-web`
3. `cp .env.example .env` และกำหนดรหัสผ่านใหม่
4. `bash scripts/prepush_gate.sh` (ใช้ Docker Compose + Python + Node) — **ต้องผ่านทั้งหมด**
5. ตรวจไฟล์ `docs/screenshots/evidence/*.json` และแคปภาพ SQL/Mongo/CRUD/rollback ตาม `docs/screenshots/README.md`
6. ทดสอบจริงบน `http://localhost:3000` รวมทั้ง upload, Background Removal, Canvas, Friendship, Borrowing และ Privacy
7. Confirm รายละเอียดที่อาจารย์ต้องตีความ: CRUD ตารางระบบภายใน, Audit Logs ไม่ให้แก้, eventual Mongo delivery

ถ้า Gate ยัง Fail, **อย่า Push โดยอ้างว่าโปรเจกต์พร้อมส่ง**. Gate ใช้ Docker Project ชื่อ `wardrobeshare-prepush` แยกจากระบบจริงและลบเฉพาะ Volumes ชุดทดสอบเท่านั้น

## ขั้นที่ 2: ตั้ง Repository หลังผ่าน Gate

```bash
git init
git branch -M main
git status --short
git add .
git diff --cached --name-only
# ห้ามมี .env, data/, models/, uploaded clothes, credentials
git commit -m 'feat: course-ready hybrid wardrobe baseline'
git remote add origin git@github.com:YOUR-ACCOUNT/YOUR-REPO.git
git push -u origin main
```

Repo ใหม่ควร Private แล้ว Invite เพื่อนและอาจารย์ (ถ้ามีสิทธิ์ตรวจผ่าน Repo) หรือ Public เฉพาะเมื่อไม่มีข้อมูลบุคคลและความลับ

## ขั้นที่ 3: เพื่อนแก้ UI ผ่าน PR

```bash
git switch main
git pull origin main
git switch -c ui/wardrobe-cards
# แก้ static/css/app.css และ static/js/app.js ตามงาน
git add static/
git commit -m 'style: refine wardrobe cards'
git push -u origin ui/wardrobe-cards
```

เปิด Pull Request เข้า `main` และต้องผ่าน Required Status Checks ทั้ง Preview tests, Browser smoke, Hybrid PostgreSQL+Mongo tests, Docker build และ vulnerability audit; ให้คนในทีม Review ก่อน Merge ไม่ต้องแก้ Backend/DB ถ้าเป็นงานตกแต่ง UI

## ขั้นที่ 4: Deploy เฉพาะเมื่อแยก Production Readiness ผ่าน

`.github/workflows/release.yml` จะ Publish Docker Web Image แบบอ้าง SHA256 Digest หลัง CI ของ `main` ผ่าน แต่ **Deploy Production ปิดโดย Default** (`DEPLOY_ENABLED` ไม่ใช่ `true`). เมื่อเซิร์ฟเวอร์ HTTPS, Secrets, Backup/Restore, Model และ GitHub Environment approval พร้อมจึงเปิดได้

- Production: Docker Compose ใช้ PostgreSQL, MongoDB และ Web เท่านั้น ไม่มี AI Microservice แยก
- Production ต้องมี `/opt/wardrobeshare/models/u2netp.onnx` หาก `REQUIRE_BACKGROUND_AI=1`
- Auto-Rollback: healthcheck fail → กลับ Web Image ก่อนหน้า; **ไม่ได้ย้อน SQL/MongoDB ข้อมูล**
- Manual Rollback: `.github/workflows/rollback.yml`; Restore DB จาก Backup เมื่อจำเป็นเท่านั้น

## กติกาที่ทีมควรล็อก

- ไม่ `docker compose down -v` กับ Project จริง
- ไม่แก้ฐานข้อมูล Production จาก PR UI
- ไม่ Commit Model Binary, Real photos, SQLite, JSON data, `.env`, SSH key
- ไม่ประกาศ “ครบทุกรายการของอาจารย์” จนมี Screenshot Proof และอาจารย์ยืนยันการตีความ Audit Logs / Technical Tables
