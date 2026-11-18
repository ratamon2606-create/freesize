# Freesize — Local CI/CD Auto-Deploy + Web Image Rollback (Ubuntu Laptop)

สถานะ: **ไฟล์พร้อมติดตั้ง แต่ยังไม่ได้ติดตั้งบนเครื่องของผู้ใช้**. ชุดนี้สร้างจาก repo `ratamon2606-create/freesize` `main` SHA `a78f8b7` (18 พ.ย. 2026). ผ่าน Python unit tests ภายในชุดไฟล์; **ยังไม่ได้ทดสอบกับ Docker/Tailscale บนโน้ตบุ๊กของผู้ใช้**.

## ภาพรวม

```text
เพื่อน push feature branch -> Pull Request
  -> Required GitHub CI 5 jobs + signed commit (ผิด = merge blocked)
  -> merge main -> main CI success -> GitHub release workflow builds GHCR image
  -> Ubuntu systemd timer (ทุก ~30 นาที) checks main SHA + successful release + publish job
  -> pulls immutable ghcr.io/ratamon2606-create/freesize-web@sha256:...
  -> tags old running web image as freesize-local-rollback:previous
  -> docker compose up --no-deps --no-build WEB ONLY
  -> HTTP localhost + Tailscale HTTPS + PostgreSQL/MongoDB readiness + invite/demo checks
  -> pass: keep web image; fail: restore old web image and PAUSE agent
```

> **จำกัด:** Health Checks ตรวจความพร้อมของ Server ไม่ได้จับทุก UI bug; ภาพเพี้ยนแต่ API healthy อาจไม่ Rollback อัตโนมัติ. Database rollback เป็นอีกระบบต่างหาก; Docker Image rollback ไม่ย้อน SQL/Mongo/uploads. ไม่รับประกัน Zero Downtime.

## Safety conditions

- จัดการเฉพาะ Compose project `freesize-friends`, service `web`, port `3100`. ไม่รัน `docker compose down -v` ไม่ Build ใหม่ ไม่ prune Images/Volumes ไม่ยุ่งกับ `wardrobeshare`, `wardrobeshare-evidence`, `source-db`, KU Tech, AXIS.
- ระบบเริ่มต้น PAUSED แม้ตั้ง systemd timer สำเร็จ; จะ Deploy **หลังสั่ง `resume` ด้วยตัวเองเท่านั้น**.
- ต้องเหลือ Root filesystem และ Docker data-root อย่างน้อย **12 GiB** ก่อน `resume` และก่อนดึง/เปลี่ยน Web Image. ณ 18 พ.ย. เครื่องผู้ใช้เหลือ **5.7 GB**; ยังเปิดอัตโนมัติไม่ได้.
- `.env.friends` ต้องมี `SEED_DEMO=0`, `WEB_PORT=3100`, `COOKIE_SECURE=1`, `PUBLIC_ORIGIN=https://<เครื่อง>.<tailnet>.ts.net`, `compose.friends.yml` ต้องส่ง `PUBLIC_ORIGIN` เข้า Web Container.
- `REQUIRE_BACKGROUND_AI=1` เมื่อโมเดล `u2netp.onnx` พร้อม; ระบบจะบังคับตรวจว่า AI พร้อมด้วย. หากยังเป็น 0 จะมีแค่ Backend + DB check (AI ยังไม่ถือว่าพร้อม).
- ไม่มี HTTPS/Tailscale Serve/Backend Healthy = **ไม่ Deploy**.
- เมื่อ Release Image ใช้งานแล้วจะเก็บ Image ก่อนหน้าไว้เป็น Tag; หาก Auto Rollback เกิด Agent หยุด Deploy จนกว่าจะตรวจสอบสาเหตุแล้ว `resume`.
- GHCR package ต้อง Public หรือ Ubuntu Docker ต้อง `docker login ghcr.io` ด้วยสิทธิ์ `read:packages` (ไม่เก็บ Token ใน repo). ห้ามส่ง Token/Key ในแชต.
- หาก `git main` เปลี่ยนกลางการ Pull Image, Agent จะหยุดรอรอบต่อไป.
- **Branch Ruleset** ต้องบังคับ Review อย่างน้อย 1 คน + Required CI 5 Jobs; ไม่บังคับ Signed Commits. Agent ค้นหา Active Ruleset ที่ครอบคลุม main อัตโนมัติ.
- GitHub release workflow ต้องทำงานสำเร็จ + งาน `Publish immutable GHCR images` สำเร็จด้วย ไม่ Deploy แค่เพราะ PR ผ่าน.

## ขั้นตอนติดตั้ง (ไม่แตะ DB เดิม)

1. **สำรองข้อมูลแยกต่างหากก่อนเริ่ม Deploy จริง** โดยเฉพาะบัญชี, รูป, PostgreSQL/MongoDB. ชุดนี้ไม่ได้ตั้ง Backup ให้โดยอัตโนมัติ. ห้ามรัน `down -v`.
2. ดาวน์โหลด `Freesize-Local-AutoDeploy-Pack-20261118.zip` ลง `~/Downloads/`.
3. บนโน้ตบุ๊ก Ubuntu:

```bash
cd ~/Downloads/freesize-deploy
git status --short                    # ตรวจการแก้ไขที่ค้างอยู่ก่อน
# ถ้า status สะอาด:
git switch -c infra/local-auto-deploy
unzip -o ~/Downloads/Freesize-Local-AutoDeploy-Pack-20261118.zip -d .

python3 -m unittest discover -s tests -p test_local_deploy_agent.py -v
python3 scripts/freesize_local_deploy.py status
python3 scripts/freesize_local_deploy.py plan
```

4. หาก `plan` ผ่านให้ติดตั้ง systemd timer (ติดตั้งแบบ **PAUSED**):

```bash
bash scripts/install_freesize_local_deploy.sh
systemctl list-timers freesize-local-deploy.timer
sudo systemctl start freesize-local-deploy.service
journalctl -u freesize-local-deploy.service -n 30 --no-pager
python3 scripts/freesize_local_deploy.py status   # ต้องบอก PAUSED
```

5. ทบทวนโค้ดใหม่เป็น PR ไม่ push ตรง `main`:

```bash
cd ~/Downloads/freesize-deploy
git add README_LOCAL_AUTODEPLOY_TH.md compose.autodeploy.yml scripts/freesize_local_deploy.py scripts/install_freesize_local_deploy.sh docs/LOCAL_AUTO_DEPLOY_TH.md tests/test_local_deploy_agent.py
git diff --cached --check
git diff --cached --stat

# Repo ที่ clone มาใหม่อาจยังไม่ตั้ง Signing Key แบบ repo-local
# ใช้ key จากการตั้ง GitHub Signing ครั้งก่อน (ถ้ามี)
git config --local gpg.format ssh
git config --local user.signingkey "$HOME/.ssh/freesize_signing"
git config --local commit.gpgsign true

git commit -m 'build: add guarded local CI deploy and rollback agent'
git push -u origin infra/local-auto-deploy
```

เปิด PR ที่ `https://github.com/ratamon2606-create/freesize/pulls`, รอ CI 5 checks ผ่าน และอนุมัติ Review ตาม Ruleset ก่อน Merge; จากนั้นบนเครื่อง Ubuntu:

```bash
git switch main
git pull --ff-only origin main
```

> ถ้า Git signing key `~/.ssh/freesize_signing` ไม่อยู่ ห้าม Commit ด้วย key path นี้ ให้ตั้ง Signing Key ตามขั้นตอน GitHub ก่อน.

## จัดการพื้นที่ก่อนเปิด Auto-Deploy

ณ ตอนตรวจ Root เหลือ 5.7 GB, Docker Images รวม ~33.75 GB, Build Cache ~1.87 GB. **อย่ารัน `docker system prune -a`, `docker volume prune` หรือ `down -v`** เพราะมี AXIS/หลักฐานและโปรเจกต์อื่นอยู่.

ตรวจแบบไม่ลบอะไร:

```bash
df -h /
docker system df -v
docker builder du 2>/dev/null || true
sudo du -xh --max-depth=1 /var/lib/docker 2>/dev/null | sort -h | tail -15
```

ค่อยตัดสินใจลบ Cache/Image ที่ยืนยันแล้วว่าไม่จำเป็น หรือย้ายที่เก็บข้อมูล **เฉพาะส่วนที่วางแผนแล้ว** ไป `/mnt/rosdata` (เดิมเหลือ ~43 GB). การย้าย Docker data-root ทั้งระบบมีผลกับทุกโปรเจกต์ จึง **ไม่ให้ทำอัตโนมัติ**.

เมื่อ Root และ Docker storage ว่าง >= 12 GiB, **Ruleset Required approvals = 1**, Model/Tailscale และเว็บเดิม healthy แล้ว ให้รัน:

```bash
cd ~/Downloads/freesize-deploy
python3 scripts/freesize_local_deploy.py resume
python3 scripts/freesize_local_deploy.py status
# ทดลอง Deploy รุ่นล่าสุดด้วยตัวเองหนึ่งรอบก่อนเปิดให้ timer จัดการ
sudo systemctl start freesize-local-deploy.service
journalctl -u freesize-local-deploy.service -n 100 --no-pager
```

ถ้า GHCR เป็น Private `docker pull` จะปฏิเสธ; ให้เปลี่ยน Package Visibility เป็น Public หรือ login ผ่านขั้นตอนที่ปลอดภัย; ห้ามวาง Token ลง .env หรือ Git.

## คำสั่งดูแลประจำวัน

```bash
cd ~/Downloads/freesize-deploy
python3 scripts/freesize_local_deploy.py status
python3 scripts/freesize_local_deploy.py plan      # read-only; GitHub latest publish
python3 scripts/freesize_local_deploy.py pause     # หยุด auto deploy
python3 scripts/freesize_local_deploy.py resume    # ต้องผ่าน disk + health ก่อน
action="rollback"
python3 scripts/freesize_local_deploy.py "$action"  # กลับสู่ Web Image ก่อนหน้า; ปิด Auto Deploy

systemctl list-timers freesize-local-deploy.timer
journalctl -u freesize-local-deploy.service -n 80 --no-pager
curl -fsS http://127.0.0.1:3100/api/ready | python3 -m json.tool
curl -fsS https://tcrose-loq-15iax9e.taila69f2a.ts.net/api/ready | python3 -m json.tool
```

หาก Timer ทำให้กังวล สามารถหยุดโดยไม่แตะ Web/DB:

```bash
python3 scripts/freesize_local_deploy.py pause
sudo systemctl disable --now freesize-local-deploy.timer
```

## Known limitations / must-do before Closed Beta

1. ปัจจุบัน Root 5.7 GB <12 GiB ดังนั้นยังไม่ให้ Auto Deploy ทำงาน.
2. Local AI เคยรายงานว่า `optional model not installed`; ต้องตรวจโมเดลและ `/api/ready` อีกครั้งก่อนบอกว่า AI พร้อม.
3. Tailscale Server อยู่บนโน้ตบุ๊ก: ถ้าเครื่องปิดหรือหลุด Tailnet เว็บก็หยุดใช้ร่วมกัน.
4. ต้องสำรอง PostgreSQL, MongoDB, Media ไปนอกเครื่อง และทดสอบ Restore ก่อนใช้ข้อมูลจริงระยะยาว.
5. การกด Merge ไม่ใช่ Preview UI; PR Review/Browser Smoke ต้องละเอียดขึ้นเพื่อจับ Visual Bug. Required Reviewer ตอนตรวจยังเป็น 0 และต้องปรับเป็น 1 ก่อน Agent เปิดใช้.
6. เรายังไม่ได้รันทดสอบ End-to-End ของ Agent บนเครื่องผู้ใช้/เครือข่ายจริง; ต้องทดสอบกับ Release จริงและจำลอง Health Check Failure บน Staging ก่อนพึ่ง Automatic Rollback.
7. ระบบนี้ Pull เฉพาะ Image ของ `main` ที่ release workflow ยืนยันสำเร็จ **แต่ภาพ Repository อาจเปลี่ยนเร็ว** หาก Action เปลี่ยนชื่อ Job/Workflow ต้องแก้การตรวจชื่อให้ตรง.
