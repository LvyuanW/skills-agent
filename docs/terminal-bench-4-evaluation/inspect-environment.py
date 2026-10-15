"""Record and verify base readiness without sending any model requests."""
import json
import os
from pathlib import Path
import subprocess


def command(*args):
    return subprocess.check_output(args, text=True).strip()


assert os.getuid() == 0
record = {
    "hostname": command("hostname"),
    "recorded_at_utc": command("date", "-u", "+%Y-%m-%dT%H:%M:%SZ"),
    "architecture": command("uname", "-m"),
    "logical_cpus": int(command("nproc")),
    "memory_total_bytes": int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:"))) * 1024,
    "data_source": command("findmnt", "-n", "-o", "SOURCE", "/data"),
    "data_filesystem": command("findmnt", "-n", "-o", "FSTYPE", "/data"),
    "data_disk_bytes": int(command("blockdev", "--getsize64", "/dev/vdb")),
    "data_available_bytes": os.statvfs("/data").f_bavail * os.statvfs("/data").f_frsize,
    "docker_packages": Path("/data/tb4/system-records/docker-packages.txt").read_text().splitlines(),
    "docker_version": command("docker", "version", "--format", "{{.Server.Version}}"),
    "compose_version": command("docker", "compose", "version", "--short"),
    "docker_data_root": command("docker", "info", "--format", "{{.DockerRootDir}}"),
    "containerd_config_root": next(line for line in Path("/etc/containerd/config.toml").read_text().splitlines() if line.startswith("root =")),
    "parent_cgroup_memory_max_bytes": int(Path("/sys/fs/cgroup/tb4.slice/memory.max").read_text()),
    "parent_cgroup_effective_cpus": Path("/sys/fs/cgroup/tb4.slice/cpuset.cpus.effective").read_text().strip(),
    "parent_cgroup_swap_max_bytes": int(Path("/sys/fs/cgroup/tb4.slice/memory.swap.max").read_text()),
    "nonroot_docker_version": command("runuser", "-u", "tbench", "--", "docker", "version", "--format", "{{.Server.Version}}"),
}
assert record["logical_cpus"] == 32
assert record["memory_total_bytes"] - record["parent_cgroup_memory_max_bytes"] >= 8 * 1024**3
assert record["parent_cgroup_memory_max_bytes"] == 115 * 1024**3
assert record["parent_cgroup_effective_cpus"] == "2-31"
assert record["parent_cgroup_swap_max_bytes"] == 0
assert record["docker_data_root"] == "/data/docker"
assert '"/data/containerd"' in record["containerd_config_root"]

name = "tb4-final-resource-smoke"
image = "alpine@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8"
cid = command("docker", "run", "-d", "--name", name, "--cpus", "1", "--memory", "128m", image, "sleep", "60")
try:
    details = json.loads(command("docker", "inspect", cid))[0]
    pid = details["State"]["Pid"]
    record["smoke_container_cgroup"] = Path(f"/proc/{pid}/cgroup").read_text().strip()
    record["smoke_container_cpu_affinity"] = next(line.split(":", 1)[1].strip() for line in Path(f"/proc/{pid}/status").read_text().splitlines() if line.startswith("Cpus_allowed_list:"))
    record["smoke_container_memory_limit_bytes"] = details["HostConfig"]["Memory"]
    assert "/tb4.slice/" in record["smoke_container_cgroup"]
    assert record["smoke_container_cpu_affinity"] == "2-31"
    assert record["smoke_container_memory_limit_bytes"] == 128 * 1024**2
finally:
    command("docker", "rm", "-f", cid)

record["controller_smoke"] = command("/usr/local/sbin/tb4-exec", "/bin/sh", "-c", "id -un; cat /proc/self/cgroup")
assert "tbench" in record["controller_smoke"] and "/tb4.slice/" in record["controller_smoke"]
for name in ("harbor", "terminal-bench"):
    record[f"{name}_commit"] = command("runuser", "-u", "tbench", "--", "git", "-C", f"/data/tb4/src/{name}", "rev-parse", "HEAD")
record["harbor_version"] = command("/data/tb4/venvs/harbor/bin/python", "-c", "from importlib.metadata import version; print(version('harbor'))")
record["python_version"] = command("/data/tb4/venvs/harbor/bin/python", "--version")
record["formal_trials_started"] = False
record["model_api_calls_made"] = 0
output = Path("/data/tb4/system-records/environment.json")
output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(record, ensure_ascii=False, indent=2))
