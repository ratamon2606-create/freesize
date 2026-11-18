# GitHub / Release Guide

กรุณาอ่าน [PRODUCTION_RELEASE_CHECKLIST_TH.md](PRODUCTION_RELEASE_CHECKLIST_TH.md)

Production ใช้ Caddy HTTPS + FastAPI in-process AI + PostgreSQL + MongoDB.
ต้องมี Invite Code, Health Check, immutable GHCR image, CI/PR และการทดสอบ
Backup/Restore. ไม่ Deploy ไป Vercel ในสถาปัตยกรรมปัจจุบัน.
