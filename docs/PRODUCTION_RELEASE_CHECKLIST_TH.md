# WardrobeShare — คู่มือ Production / Closed Beta

> **สถานะไฟล์:** โค้ดเตรียมพร้อมสำหรับทดลอง Closed Beta หลังติดตั้ง VPS, Domain,
> SSL, GHCR, Secrets และทดสอบจริงบนเซิร์ฟเวอร์แล้วเท่านั้น **ยังไม่ได้ Deploy จาก ChatGPT**
> และยังไม่ได้ทดสอบ Docker Production stack ใน Runtime ของผู้จัดทำไฟล์นี้
>
> **เลือก VPS ไม่ใช้ Vercel**: FastAPI + local AI model + persistent uploaded photos
> + PostgreSQL + MongoDB ต้องการ durable server filesystem และ long-running services.
> Vercel Functions มีไฟล์เขียนได้เฉพาะ temporary scratch จึงจะต้องย้าย media/database
> ไป external hosted services ซึ่งไม่ใช่สถาปัตยกรรมนี้และอาจขัดเงื่อนไขงานอาจารย์

## ภาพรวม Flow

```
UI branch -> Pull Request -> 5 required CI jobs -> review -> merge main
         -> successful main CI -> GHCR image by immutable sha256 digest
         -> GitHub environment approval (if plan supports it)
         -> SSH VPS -> deploy web image -> internal + HTTPS health
         -> external GitHub Runner smoke -> success or rollback to previous image
```

**ไม่ทำ `docker compose down -v` ใน Production**. เปลี่ยน Docker image ไม่ใช่การย้อน DB;
ต้อง Backup/ทดสอบ Restore แยก และไม่ใช้ destructive migrations ใน release ทั่วไป.

## A. GitHub Repository / การทำงานร่วมกัน

1. สร้าง Repo ใหม่บน GitHub จะเป็น Private ก็ได้; ให้ผู้ดูแลเริ่ม `main` ด้วย README
   ก่อนสร้าง feature branch; ตรวจ `.gitignore` และ `git status` ไม่ให้มี secrets/รูปส่วนตัว
2. จาก ZIP ใหม่:

```bash
cd wardrobeshare-web
git init
git remote add origin git@github.com:OWNER/REPO.git
git fetch origin
git switch -c feature/initial-wardrobeshare origin/main
git add .
git diff --cached --name-only
git commit -m 'feat: closed-beta safe baseline with CI and rollback'
git push -u origin feature/initial-wardrobeshare
```

3. เปิด PR สู่ `main`; Settings -> Rules -> Rulesets หรือ Branch protection:
   Require PR, Require at least 1 review, Require status checks:
   `Unit and API (preview)`, `Browser UI smoke`, `Hybrid DB (PostgreSQL + MongoDB)`,
   `Build container images`, `Dependency vulnerability audit`.
4. ให้เพื่อนเป็น Collaborator และทำงาน branch `ui/...` ทุกครั้ง อย่า Push main ตรง.
5. GHCR package ต้องให้ VPS ดึงได้: ทำแพ็กเกจ Public **หรือ** ลงทะเบียน GitHub PAT
   ที่มี `read:packages` บน VPS ด้วย `docker login ghcr.io` (ห้ามเก็บ token ใน repo).
   ตรวจว่า GitHub Actions publish package ได้ด้วย `GITHUB_TOKEN`.

## B. เตรียม Ubuntu VPS แบบแยกจากเครื่องมหาวิทยาลัยและฐานทดสอบ

แนะนำ Ubuntu VPS ที่มี RAM อย่างน้อยประมาณ 8GB สำหรับ Web + Mongo + Postgres
+ CPU AI และ SSD เพียงพอสำหรับรูปจริง (ประเมินจากจำนวนเพื่อน/รูป), พร้อม Domain/DNS
ที่ชี้ A/AAAA ไปเซิร์ฟเวอร์ ตั้ง Firewall ให้เปิดเฉพาะ SSH ที่จำเป็น, TCP 80/443,
และ UDP 443 (HTTP/3 ไม่จำเป็น สามารถปิด UDP หากไม่ใช้). อย่าเปิด 5432 หรือ 27017 สู่ Internet.
ติดตั้ง Docker Engine และ Compose plugin ด้วยวิธีทางการ; ใช้บัญชี SSH ที่ควบคุมสิทธิ์.

เตรียมไฟล์ใน **server** (`/opt/wardrobeshare`) ด้วย `scp` หรือ `rsync`:

```
/opt/wardrobeshare/
  compose.production.yml       # copied from deploy/compose.production.yml
  Caddyfile                    # copied from deploy/Caddyfile
  deploy-release.sh            # copied from deploy/deploy-release.sh
  rollback-release.sh          # copied from deploy/rollback-release.sh
  verify-config.sh             # copied from deploy/verify-config.sh
  bootstrap-vps.sh             # copied from deploy/bootstrap-vps.sh
  backup-data.sh               # copied from deploy/backup-data.sh
  .env                         # private, copied from deploy/.env.production.example and edited
  models/u2netp.onnx           # trusted pretrained model, verified locally
  state/                       # releases, populated by deployment scripts
  backups/                     # private local copies; also send off-host
```

ตัวอย่าง (บนเครื่องเรา):

```bash
# ในโฟลเดอร์ wardrobeshare-web
ssh deploy@YOUR_VPS 'sudo mkdir -p /opt/wardrobeshare && sudo chown deploy:deploy /opt/wardrobeshare'
scp deploy/compose.production.yml deploy/Caddyfile deploy/*.sh deploy/*.timer deploy/*.service deploy/.env.production.example deploy@YOUR_VPS:/opt/wardrobeshare/
scp models/u2netp.onnx deploy@YOUR_VPS:/opt/wardrobeshare/models/u2netp.onnx
```

ก่อน `scp` โมเดล ให้สร้าง `/opt/wardrobeshare/models` ฝั่ง VPS (`mkdir -p`).
ย้ายไฟล์ `.env.production.example` เป็น `.env` บน VPS และกำหนด Domain + Secrets
แบบสุ่มสามตัว (แต่ละตัว `openssl rand -hex 32`) และตั้ง `chmod 600 .env`.
ตั้ง `WEB_IMAGE` เป็น GHCR digest ที่ Publish แล้วจริง (ไม่ใช้ `latest`).

บน VPS:

```bash
cd /opt/wardrobeshare
bash verify-config.sh
bash bootstrap-vps.sh
docker compose --env-file .env -f compose.production.yml ps
curl -fsS "$(grep '^PUBLIC_ORIGIN=' .env | cut -d= -f2-)/api/ready"
```

**ข้อห้าม:** อย่า Copy Demo PostgreSQL/MongoDB volumes หรือ .env จากเครื่องทดสอบมาบน VPS.
ยืนยันเว็บเปิดขึ้นแล้ว: ไม่มีเพื่อนปลอม, รูปเสื้อผ้าปลอม หรือ Demo Login.
รูปตกแต่ง Static Assets ใน Login ไม่ใช่ Demo Records (เปลี่ยน UI ภายหลังได้).

## C. GitHub Production Gate

ใน Repository -> Settings -> Secrets and variables -> Actions:

**Secrets**:
- `PROD_SSH_HOST` = IP หรือ hostname VPS
- `PROD_SSH_USER` = deploy user บน VPS
- `PROD_SSH_PRIVATE_KEY` = SSH private key dedicated สำหรับ Actions
- `PROD_SSH_KNOWN_HOSTS` = `ssh-keyscan` output **ตรวจ fingerprint นอกช่องทางก่อน**

**Variables**:
- `DEPLOY_ENABLED` = `false` ระหว่างเตรียม (ค่อยเป็น `true` หลัง Bootstrap/Backup/Smoke)
- `PROD_PUBLIC_ORIGIN` = `https://YOUR_DOMAIN` (ไม่ต้องมี slash ท้าย)

สร้าง Environment ชื่อ `production` กำหนด Reviewers ถ้า Plan/Repo รองรับ.
**บางแผนไม่รองรับ Required Reviewers บน Private Repo**: ต้องให้ใช้ Manual
Approval จากผู้ดูแล Merge หรือเปลี่ยน Repo/Plan ไม่ใช่อ้างว่าปลอดภัยโดยอัตโนมัติ.

หลัง Merge แล้ว CI green -> image published. เมื่อ Bootstrap ผ่าน + Backup ผ่าน
ให้ตั้ง `DEPLOY_ENABLED=true`. Release รอบต่อไปที่ผ่าน CI และ environment gate
จะ deploy โดยอัตโนมัติ. เพื่อ Deploy image แรก อาจใช้ initial image บน VPS
ผ่าน Bootstrap ก่อน และรอ Release รอบถัดไปเพื่อทดสอบ Auto Rollback.

**Rollback:** GitHub -> Actions -> Manual production rollback -> Run workflow
ถ้า image ใหม่ Health Check ไม่ผ่าน `deploy-release.sh` จะย้อนกลับ image เก่าเอง.
หมายเหตุ: รอบแรกที่ไม่มี previous image ยังไม่มี rollback อัตโนมัติ.

## D. Backup / Restore

บน VPS:

```bash
sudo bash /opt/wardrobeshare/backup-data.sh
# backup SQL + Mongo + uploaded photos, check SHA256 checksums
```

เพิ่ม systemd timer ที่ผู้ดูแลอนุญาต:

```bash
sudo cp /opt/wardrobeshare/wardrobeshare-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wardrobeshare-backup.timer
sudo systemctl list-timers | grep wardrobeshare
```

**ต้องเก็บ Backup เข้ารหัสนอก VPS** ด้วยวิธีที่ผู้ดูแลควบคุมและทดลอง Restore
ไป Disposable Environment ก่อน (ดู `restore-runbook.md`). ไม่มี Automatic DB
Rollback เพราะ SQL/Mongo อาจมีข้อมูลใหม่หลังการ Deploy. ระวัง schema migrations
ต้อง backward-compatible กับ image ก่อนหน้า.

## E. Manual Closed Beta Acceptance (ต้องผ่านก่อนแชร์ URL)

- [ ] HTTPS ผ่าน Browser, ไม่มี Mixed Content, cookies Secure + HttpOnly
- [ ] ไม่มี Demo accounts / Demo data (ใช้ฐานใหม่และ `SEED_DEMO=0`)
- [ ] ไม่มี `/.env`, SSH key, Database port หรือ private upload URL เปิดสาธารณะ
- [ ] สมัคร User A ด้วย Invite Code; ไม่ใส่/ใส่ผิด ต้องสมัครไม่ได้
- [ ] User B ใช้ Invite Code สมัคร, อัปโหลด JPEG/PNG, AI ตัดพื้นหลังได้จริง
- [ ] User A ไม่เห็นรูปส่วนตัวของ B จนเป็นเพื่อน, ไม่เห็น OOTD ที่มีเจ้าของคนอื่นที่ไม่ใช่เพื่อน
- [ ] User A/B เพิ่มเพื่อน, จัด OOTD, Save/Edit/Delete, ยืม/ส่งมอบ/คืน
- [ ] Logout/Login ข้าม Browser และ Restart Container แล้วข้อมูล+รูปยังอยู่
- [ ] Login ที่ผิดซ้ำถูก Rate-limit; Upload SVG/oversized ถูกปฏิเสธ
- [ ] Backup SQL+Mongo+Photos ผ่าน และทดลอง Restore แยก
- [ ] CI ทุก Job ผ่าน, Branch Protection active, ขยายลง Production หลัง Approval เท่านั้น
- [ ] จำลองปล่อย image ที่ health fail แล้วเห็น Auto-Rollback **บน VPS test/staging**

## ข้อจำกัด / งานระยะถัดไป

- ยังไม่มี Password Reset/Email Verification (เหมาะสำหรับ Closed Beta ที่มีผู้ดูแล);
  ผู้ใช้ต้องจดรหัสผ่านให้ปลอดภัย และมีขั้นตอนยืนยันตัวตนเจ้าของก่อนช่วยกู้บัญชี.
- Rate Limiter นี้อยู่ใน memory **ของ web process เดียว**; ถ้าขยายหลาย replicas
  ควรใช้ shared rate limiter. อย่าเปิดจำนวน workers >1 โดยไม่แก้.
- ไม่มีการรับประกัน Zero Downtime ระหว่าง Web Container swap; มี downtime สั้น ๆ ได้.
- GHCR private image needs registry login on VPS; Pipeline ไม่ควร Push ไปเซิร์ฟเวอร์
  ที่ยังไม่ได้ Bootstrap/เปิด firewall/ตั้ง DNS ให้ถูกต้อง.
- ต้องตรวจข้อกำหนดอาจารย์แยกต่างหาก: CR​UD ของ audit/technical entities และ
  screenshot, video, slides ไม่ใช่เงื่อนไขที่ Pipeline ยืนยันแทนได้.
