# NFS Backup — Server Setup

The Backup & Recovery feature's **NFS Share** location mounts the remote share with `mount -t nfs` before writing the archive, then unmounts it afterward. Mounting a filesystem is a privileged Linux operation — the app does not (and should not) run as root, so this one-time setup grants it a narrowly-scoped, passwordless `sudo` rule for exactly that.

**Local Folder backups need none of this** — only NFS does.

## Automated setup

Run on the host that actually runs the app (not a dev checkout), as the normal user the app runs as — not via `sudo`:

```bash
bash tools/setup_nfs_backup.sh
```

It detects the app's install directory and current user automatically (asks for confirmation, or a different username, before changing anything), then installs two root-owned wrapper scripts and a sudoers rule. It calls `sudo` itself for the privileged steps, so it will prompt for your password during the run.

## What it installs, and why

- `/usr/local/sbin/quant-backup-nfs-mount` and `-umount` — small scripts that mount/unmount **only** the app's own scratch mountpoint (`<app dir>/data/.nfs_backup_mount`), rejecting any other path. This means the sudoers grant below can't be used to mount or unmount anything else on the host.
- `/etc/sudoers.d/quant-backup-nfs` — grants the app's user passwordless `sudo` for exactly those two scripts (validated with `visudo -c` before being installed, so a malformed rule can never reach the live sudoers directory).

## Manual setup (if you'd rather not run the script)

```bash
sudo tee /usr/local/sbin/quant-backup-nfs-mount > /dev/null << 'EOF'
#!/bin/bash
set -euo pipefail
SOURCE="$1"
MOUNTPOINT="$2"
case "$MOUNTPOINT" in
  /path/to/app/data/.nfs_backup_mount) ;;
  *) echo "Refusing to mount onto unexpected path: $MOUNTPOINT" >&2; exit 1 ;;
esac
mkdir -p "$MOUNTPOINT"
mount -t nfs "$SOURCE" "$MOUNTPOINT"
EOF

sudo tee /usr/local/sbin/quant-backup-nfs-umount > /dev/null << 'EOF'
#!/bin/bash
set -euo pipefail
MOUNTPOINT="$1"
case "$MOUNTPOINT" in
  /path/to/app/data/.nfs_backup_mount) ;;
  *) echo "Refusing to unmount unexpected path: $MOUNTPOINT" >&2; exit 1 ;;
esac
umount "$MOUNTPOINT"
EOF

sudo chmod 755 /usr/local/sbin/quant-backup-nfs-mount /usr/local/sbin/quant-backup-nfs-umount
sudo chown root:root /usr/local/sbin/quant-backup-nfs-mount /usr/local/sbin/quant-backup-nfs-umount

sudo visudo -f /etc/sudoers.d/quant-backup-nfs
```

Replace `/path/to/app/data/.nfs_backup_mount` with the real path (the app's install directory + `/data/.nfs_backup_mount`). In the `visudo` editor that opens, paste (replacing `your_user`):

```
your_user ALL=(root) NOPASSWD: /usr/local/sbin/quant-backup-nfs-mount
your_user ALL=(root) NOPASSWD: /usr/local/sbin/quant-backup-nfs-umount
```

## Testing it directly

Before trusting the app with it, confirm the mount actually works:

```bash
sudo -n /usr/local/sbin/quant-backup-nfs-mount <server>:<export-path> /path/to/app/data/.nfs_backup_mount
mount | grep nfs_backup_mount
sudo -n /usr/local/sbin/quant-backup-nfs-umount /path/to/app/data/.nfs_backup_mount
```

`mount -t nfs` requires `host:dir` format — a colon, not a slash, between the server and the export path. In the app's own Settings UI, **NFS Server** and **NFS Export Path** are separate fields and the colon is joined automatically; this only matters when testing the script by hand.

## Multiple deployments on one host

Each deployment (e.g. a dev checkout and a production checkout) has its own install directory and therefore its own mountpoint. The wrapper scripts only accept the one mountpoint baked in when `setup_nfs_backup.sh` was last run — if you run it again for a second deployment on the same host, it overwrites the previous one. Only the deployment that actually needs NFS backups should run this script.
