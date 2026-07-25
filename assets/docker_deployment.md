# Docker Deployment

The app can run as a Docker container instead of a bare-metal `venv` install. This is an alternative to the systemd service setup in `README.md`, not a replacement for it — pick one.

## How it's built

`.github/workflows/docker-build.yml` builds and pushes an image to GitHub Container Registry (GHCR) on every push to `main`. No separate "build" repository is involved — the Dockerfile and workflow live in this repo, next to the code they package.

Each successful run pushes two tags of the same image:

- `ghcr.io/alfwro13/stock_analysis_project:latest` — always the most recent `main`.
- `ghcr.io/alfwro13/stock_analysis_project:<7-char-git-sha>` — immutable, lets you pin or roll back to an exact build (`docker pull ghcr.io/alfwro13/stock_analysis_project:a1b2c3d`).

There is no version number anywhere in the app (no `VERSION` file, no `__version__`) — the git sha tag is the identifier. This was a deliberate choice over introducing a manually-bumped semantic version, which would need remembering to update and nothing currently enforces that.

Image is built for `linux/amd64` only.

**First-time GHCR visibility:** a package pushed with the default `GITHUB_TOKEN` is created **private** the first time, even though this source repo is public. After the first successful workflow run, go to the package's page on GitHub (your profile → Packages → `stock_analysis_project`) → Package settings → change visibility to Public if you want to `docker pull` without authenticating on the server. If you'd rather keep it private, `docker login ghcr.io` on the server with a GitHub PAT that has `read:packages` scope before pulling.

## What's baked into the image vs. mounted from the host

Everything under version control (`*.py`, `templates/`, `static/` except the two generated paths below, `assets/`) is baked into the image at build time. Runtime state is never baked in — it's bind-mounted from the host so it survives container recreation and image updates:

| Host path | Container path | What it is |
|---|---|---|
| `./data` | `/app/data` | SQLite DB (`analysis.db`), Parquet price history, fundamentals JSON dumps |
| `./models` | `/app/models` | Trained ML artifacts (`.joblib`) |
| `./logs` | `/app/logs` | Rotating `app.log` |
| `./backups` | `/app/backups` | Local Backup & Recovery destination |
| `./config.json` | `/app/config.json` | Runtime settings (auto-created on first boot if the mounted file is empty) |
| `.env` | (loaded via `env_file`) | All secrets — dashboard credentials, API keys, Nextcloud/Ghostfolio/FRED/HF tokens |

`reports/` and `static/briefing_charts/` are **not** mounted — both are dead leftovers from a retired feature; nothing in the current codebase reads or writes to them (confirmed by grep, 2026-07-25). `static/js/mermaid.min.js` is baked into the image at build time rather than left to self-heal via its runtime CDN download (`utils.py`'s `ensure_workflow_assets()`), so the container doesn't need outbound internet just to render the Workflow Monitor page — `Dockerfile`'s `apt-get install curl` is only there for the container `HEALTHCHECK`, the mermaid file itself is copied in via `COPY . .` from the git-tracked repo.

**NFS backups need a different path in a container.** The Backup & Recovery feature's NFS Share destination mounts the remote share per-run via `sudo -n` wrapper scripts on the host (see `assets/nfs_backup_setup.md`) — that requires a privileged, unsandboxed process, which a normal Docker container doesn't have and shouldn't be given just for this. The app's NFS Share option is therefore bare-metal-only; don't select it when running in Docker.

Instead, mount the NFS share **permanently on the Docker host** (outside the container, once, not per-backup-run) and bind it into the container in place of the local `./backups` mount — the app then sees an ordinary directory and you use its existing **Local Folder** destination, exactly as if it were local disk. No app code changes.

On the host, add a normal `/etc/fstab` entry (adjust `server:/export/path` and options for your NFS server):

```
server:/export/path  /mnt/quant-backup-nfs  nfs  defaults,_netdev  0  0
```

```bash
sudo mkdir -p /mnt/quant-backup-nfs
sudo mount -a
```

Then point `docker-compose.yml`'s backup volume at that host path instead of `./backups`:

```yaml
    volumes:
      - /mnt/quant-backup-nfs:/app/backups   # instead of ./backups:/app/backups
```

In the app's Settings → Backup & Recovery, choose **Local Folder** (not NFS Share) — it will write straight into `/app/backups`, which on the host is really the NFS share. The host owns the mount's lifecycle (it's up before Docker starts and stays up), so there's no per-run mount/unmount step for the app to worry about, and the existing sudoers-gated wrapper scripts in `assets/nfs_backup_setup.md` aren't needed for a Docker deployment at all.

## Running it

```bash
mkdir -p data models logs backups
touch config.json          # first boot fills this in
cp .env.example .env       # then fill in real values
docker compose up -d
```

`docker-compose.yml` maps port `8090:8090` and reads secrets from `.env` via `env_file`. Edit the `image:` tag if you want to pin a specific build instead of `:latest`.

Updating to the newest build:

```bash
docker compose pull
docker compose up -d
```

## Why not a separate "docker-build" repository

A second repository whose only job is building the image was considered and rejected — this repo is already public, holds no secrets (all of those live in `.env`/`config.json`, both gitignored and excluded from the build context via `.dockerignore`), and a same-repo workflow avoids the extra complexity of one repo triggering a build in another (cross-repo `repository_dispatch`, a checkout token, keeping two repos' branches in sync). If a genuine reason to split them ever comes up, revisit then rather than pre-building the cross-repo plumbing now.
