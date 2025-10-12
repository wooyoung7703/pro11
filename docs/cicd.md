# CI/CD (GitHub Actions → Docker → GHCR)

This repo auto-builds Docker images on push to `main` and can auto-deploy to a server via SSH.

## Overview
- Build workflow: `.github/workflows/docker-build.yml`
  - Builds `backend` and `frontend` images
  - Pushes to GitHub Container Registry (GHCR)
  - Tags: `latest` and `${GITHUB_SHA}`
- Deploy workflow: `.github/workflows/deploy.yml`
  - Triggers after a successful build (workflow_run)
  - SSH to your server and `docker compose -f docker-compose.prod.yml up -d`

## Prerequisites
- Create a GitHub environment or repo secrets:
  - DEPLOY_HOST: your server IP/hostname
  - DEPLOY_USER: SSH user on the server
  - DEPLOY_SSH_KEY: private key for SSH (use a deploy key or personal key)
- On the server:
  - Docker and Docker Compose installed
  - A working directory (e.g. `~/pro11`) with `docker-compose.prod.yml` and `.env` files
  - Login to GHCR is handled by the workflow on each deploy

## Server files
Use `docker-compose.prod.yml` in the repo root. It references GHCR images:

```yaml
services:
  app:
    image: ghcr.io/<owner>/pro11-backend:latest
    env_file:
      - .env
      - .env.private
    ports:
      - "8000:8000"
  frontend:
    image: ghcr.io/<owner>/pro11-frontend:latest
    ports:
      - "8080:80"
```

## Manual deploy
You can trigger from Actions → deploy → Run workflow.

## Notes
- If your server cannot access the repo files, scp them once or clone the repo to `~/pro11`.
- For private GHCR pulls from the server manually, run `echo <TOKEN> | docker login ghcr.io -u <USER> --password-stdin`.
- Customize tags, cache, and multi-arch as needed.
