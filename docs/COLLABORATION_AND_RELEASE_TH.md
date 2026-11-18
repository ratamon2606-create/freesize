# GitHub / Release Guide

กรุณาอ่าน [PRODUCTION_RELEASE_CHECKLIST_TH.md](PRODUCTION_RELEASE_CHECKLIST_TH.md)

Production ใช้ Caddy HTTPS + FastAPI in-process AI + PostgreSQL + MongoDB.
ไม่ใช้ Invite Code แล้ว; ต้องจำกัดการเข้าเว็บด้วย Tailscale, มี Health Check, immutable GHCR image, CI/PR และการทดสอบ
Backup/Restore. ไม่ Deploy ไป Vercel ในสถาปัตยกรรมปัจจุบัน.
