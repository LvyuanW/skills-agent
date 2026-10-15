#!/usr/bin/env bash
# Base provisioning for the three explicitly supplied fresh Ubuntu 22.04 hosts.
# No model API requests, credentials, or evaluation trials are launched here.
set -euo pipefail
export LC_ALL=C LANG=C DEBIAN_FRONTEND=noninteractive
umask 022

[[ $(id -u) == 0 ]]
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 22.04 ]]
[[ $(nproc) == 32 ]]
[[ $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]]

# This entry point is deliberately restricted to the verified empty data disk.
if ! mountpoint -q /data; then
  [[ -b /dev/vdb ]]
  [[ -z $(wipefs --no-act --noheadings /dev/vdb) ]]
  [[ $(lsblk -nrpo NAME /dev/vdb | wc -l) == 1 ]]
  python3 - <<'PY'
from pathlib import Path
import subprocess
with open('/dev/vdb', 'rb', buffering=0) as disk:
    size = disk.seek(0, 2)
    assert size == 536870912000, size
    disk.seek(0)
    assert not any(disk.read(1048576)), 'Nonempty disk header'
    disk.seek(size - 1048576)
    assert not any(disk.read(1048576)), 'Nonempty disk trailer'
assert not Path('/data').exists() or not any(Path('/data').iterdir())
PY
  mkfs.ext4 -m 0 -L tb4-data /dev/vdb
  install -d -m 0755 /data
  cp -a /etc/fstab /etc/fstab.before-tb4
  tb4_uuid=$(blkid -s UUID -o value /dev/vdb)
  printf 'UUID=%s /data ext4 defaults,nofail,x-systemd.device-timeout=10 0 2\n' "$tb4_uuid" >> /etc/fstab
  mount /data
fi
[[ $(findmnt -n -o SOURCE /data) == /dev/vdb ]]

apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg git \
  rsync jq tmux unzip xz-utils build-essential pkg-config libssl-dev libffi-dev \
  python3-venv python3-pip sysstat
install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: jammy
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update -qq

# Resolve all package versions once on each identical mirror, record and hold.
tb4_docker_packages=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
tb4_docker_install=()
for tb4_pkg in "${tb4_docker_packages[@]}"; do
  tb4_version=$(apt-cache policy "$tb4_pkg" | awk '/Candidate:/ {print $2}')
  [[ -n $tb4_version && $tb4_version != '(none)' ]]
  tb4_docker_install+=("$tb4_pkg=$tb4_version")
done
apt-get install -y -qq --no-install-recommends "${tb4_docker_install[@]}"
apt-mark hold "${tb4_docker_packages[@]}"
systemctl stop docker.service docker.socket containerd.service

install -d -m 0755 /data/docker /data/containerd /data/tb4 /etc/docker \
  /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
cat > /etc/systemd/system/tb4.slice <<'EOF'
[Unit]
Description=Terminal-Bench evaluation aggregate resource boundary

[Slice]
AllowedCPUs=2-31
CPUQuota=3000%
MemoryAccounting=yes
MemoryMax=115G
MemorySwapMax=0
TasksMax=infinity
EOF
for tb4_service in docker containerd; do
  cat > "/etc/systemd/system/$tb4_service.service.d/tb4.conf" <<'EOF'
[Unit]
RequiresMountsFor=/data

[Service]
Slice=tb4.slice
EOF
done
cat > /etc/docker/daemon.json <<'EOF'
{
  "data-root": "/data/docker",
  "exec-opts": ["native.cgroupdriver=systemd"],
  "cgroup-parent": "tb4.slice",
  "log-driver": "local",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
EOF
containerd config default > /etc/containerd/config.toml
python3 - <<'PY'
from pathlib import Path
import re
p = Path('/etc/containerd/config.toml')
s, count = re.subn(r'''(?m)^root = ['"]/var/lib/containerd['"]$''',
                   'root = "/data/containerd"', p.read_text())
assert count == 1, 'Unexpected containerd root configuration'
p.write_text(s)
PY
rsync -aHAX /var/lib/containerd/ /data/containerd/
systemctl daemon-reload
systemctl start tb4.slice
systemctl enable --now containerd.service docker.service

install -d -m 0755 /data/home
if ! id tbench >/dev/null 2>&1; then
  useradd --create-home --home-dir /data/home/tbench --shell /bin/bash tbench
fi
install -d -o tbench -g tbench -m 0750 /data/home/tbench
usermod -aG docker tbench
install -d -o tbench -g tbench -m 0755 /data/tb4/{src,venvs,cache,runs,artifacts,releases,plans,logs}
install -d -o tbench -g tbench -m 0700 /data/tb4/secrets
install -d -m 0755 /data/tb4/system-records
dpkg-query -W -f='${Package}=${Version}\n' "${tb4_docker_packages[@]}" > /data/tb4/system-records/docker-packages.txt
cp /etc/docker/daemon.json /data/tb4/system-records/docker-daemon.json
cp /etc/systemd/system/tb4.slice /data/tb4/system-records/tb4.slice
df -hT /data
docker version --format '{{.Server.Version}}'
docker compose version
systemctl show tb4.slice -p AllowedCPUs -p EffectiveCPUs -p MemoryMax -p MemorySwapMax
printf 'TB4_BASE_PROVISIONED\n'
