# Freesize Local Auto-Deploy Pack (18 November 2026)

ชุดไฟล์ *เพิ่มใหม่เท่านั้น* สำหรับโฟลเดอร์ `~/Downloads/freesize-deploy` จาก GitHub Repo `ratamon2606-create/freesize`.

- `scripts/freesize_local_deploy.py` — ตรวจ GHCR, Deploy Web เท่านั้น, Health Check, Auto-Rollback และ Pause
- `scripts/install_freesize_local_deploy.sh` — ติดตั้ง systemd timer ทุก 30 นาที, **Paused** ตั้งแต่เริ่ม
- `compose.autodeploy.yml` — Compose override เพื่อใช้ Docker Image ตาม digest โดยไม่ Build
- `tests/test_local_deploy_agent.py` — 8 safety unit tests
- `docs/LOCAL_AUTO_DEPLOY_TH.md` — คู่มือคำสั่งและข้อควรระวังภาษาไทย

เริ่มด้วยการอ่าน `docs/LOCAL_AUTO_DEPLOY_TH.md` ก่อน **ห้ามเปิด `resume` หาก Root ยังเหลือ 5.7 GB**.

ไม่มี `.env`, Secret, Image, Container, Volume, Model หรือฐานข้อมูลรวมอยู่ใน ZIP.
