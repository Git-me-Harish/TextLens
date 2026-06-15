""Redis isn't running. Nothing to do with code — the service just isn't started on your Windows machine"" \

Fix — install and start Redis on Windows:
Redis doesn't run natively on Windows. Two options:

Option 1 — WSL (recommended, permanent):
bash# In WSL terminal:

> sudo apt update && sudo apt install redis-server -y
> sudo service redis-server start

## Verify

> redis-cli ping  # should return PONG

Then run Celery from your Windows PowerShell as normal — it connects to WSL's Redis on localhost:6379.

Option 2 — Docker (if you have Docker Desktop):

> bashdocker run -d --name redis -p 6379:6379 redis:alpine

Option 3 — Memurai (Redis for Windows native):
-- Download from memurai.com — drop-in Redis for Windows, installs as a service.

Verify Redis is up before starting Celery:

> bashredis-cli ping

## PONG = good, start celery worker + beat
FastAPI also uses Redis (rate limiting etc.) — check your .env has:
REDIS_URL=redis://localhost:6379/0