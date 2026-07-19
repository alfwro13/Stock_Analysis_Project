#!/bin/bash
# One-time host setup for the Backup & Recovery feature's NFS option.
# Run as the normal user the app runs as (not via sudo) — it calls sudo itself
# for the privileged steps, so it can still detect the correct non-root user.
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this as the user the app runs as, not as root/sudo — it calls sudo itself where needed." >&2
    exit 1
fi

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNTPOINT="${BASE_DIR}/.nfs_backup_mount"
MOUNT_SCRIPT="/usr/local/sbin/quant-backup-nfs-mount"
UMOUNT_SCRIPT="/usr/local/sbin/quant-backup-nfs-umount"
SUDOERS_FILE="/etc/sudoers.d/quant-backup-nfs"

echo "Quantamental Dashboard — NFS Backup setup"
echo "App directory detected: ${BASE_DIR}"
echo "Mountpoint will be:     ${MOUNTPOINT}"
echo

default_user="$(whoami)"
read -rp "Which OS user does the app run as? [${default_user}]: " APP_USER
APP_USER="${APP_USER:-${default_user}}"

if ! id "${APP_USER}" >/dev/null 2>&1; then
    echo "User '${APP_USER}' does not exist on this system. Aborting." >&2
    exit 1
fi

echo
echo "This will install (asking for your sudo password as needed):"
echo "  - ${MOUNT_SCRIPT}"
echo "  - ${UMOUNT_SCRIPT}"
echo "  - ${SUDOERS_FILE}  (grants ${APP_USER} passwordless mount/umount via the two scripts above, restricted to ${MOUNTPOINT})"
echo
read -rp "Continue? [y/N]: " CONFIRM
if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
    echo "Aborted — nothing was changed."
    exit 0
fi

# Each wrapper only accepts its one known mountpoint, baked in at install time, so the
# sudoers NOPASSWD grant below can't be used to mount/unmount anything else on the host.
sudo tee "${MOUNT_SCRIPT}" > /dev/null << EOF
#!/bin/bash
set -euo pipefail
SOURCE="\$1"
MOUNTPOINT="\$2"
case "\$MOUNTPOINT" in
  ${MOUNTPOINT}) ;;
  *) echo "Refusing to mount onto unexpected path: \$MOUNTPOINT" >&2; exit 1 ;;
esac
mkdir -p "\$MOUNTPOINT"
mount -t nfs "\$SOURCE" "\$MOUNTPOINT"
EOF

sudo tee "${UMOUNT_SCRIPT}" > /dev/null << EOF
#!/bin/bash
set -euo pipefail
MOUNTPOINT="\$1"
case "\$MOUNTPOINT" in
  ${MOUNTPOINT}) ;;
  *) echo "Refusing to unmount unexpected path: \$MOUNTPOINT" >&2; exit 1 ;;
esac
umount "\$MOUNTPOINT"
EOF

sudo chmod 755 "${MOUNT_SCRIPT}" "${UMOUNT_SCRIPT}"
sudo chown root:root "${MOUNT_SCRIPT}" "${UMOUNT_SCRIPT}"

TMP_SUDOERS="$(mktemp)"
trap 'rm -f "${TMP_SUDOERS}"' EXIT
cat > "${TMP_SUDOERS}" << EOF
${APP_USER} ALL=(root) NOPASSWD: ${MOUNT_SCRIPT}
${APP_USER} ALL=(root) NOPASSWD: ${UMOUNT_SCRIPT}
EOF

# visudo -c validates syntax before anything touches the live sudoers.d directory,
# so a typo here can't break sudo on the host.
if ! sudo visudo -c -f "${TMP_SUDOERS}"; then
    echo "Generated sudoers file failed validation — aborting without installing it." >&2
    exit 1
fi

sudo install -m 0440 -o root -g root "${TMP_SUDOERS}" "${SUDOERS_FILE}"

echo
echo "Done. Test it directly before trusting the app with it:"
echo "  sudo -n ${MOUNT_SCRIPT} <server>:<path> ${MOUNTPOINT}"
echo "  mount | grep nfs_backup_mount"
echo "  sudo -n ${UMOUNT_SCRIPT} ${MOUNTPOINT}"
echo
echo "Then set NFS Server / NFS Export Path in Settings -> Backup & Recovery and click 'Run Backup Now'."
