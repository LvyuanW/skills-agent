"""Inspect CA roots in available selected images with networking disabled."""

from pathlib import Path
import json
import subprocess

CHECK = r'''import ssl,json,pathlib,re
p=ssl.get_default_verify_paths()
d=pathlib.Path(p.capath) if p.capath else None
print(json.dumps({'verify_paths':p._asdict(),'loaded_store':ssl.create_default_context().cert_store_stats(),'hashed_ca_entries':sum(bool(re.fullmatch(r'[0-9a-f]{8}\.[0-9]+',x.name)) for x in d.iterdir()) if d and d.is_dir() else 0,'ca_files':{x:pathlib.Path(x).exists() for x in ['/etc/ssl/cert.pem','/etc/ssl/certs/ca-certificates.crt','/etc/pki/tls/certs/ca-bundle.crt','/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem']},'os_release':pathlib.Path('/etc/os-release').read_text().splitlines()[:3]}))'''


def main():
    names = Path('/data/tb4/plans/terminal-bench-4-evaluation/tasks.txt').read_text().split()
    images = subprocess.check_output(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], text=True).splitlines()
    records = []
    for name in names:
        directory = Path('/data/tb4/src/terminal-bench/tasks') / name / 'environment'
        dockerfiles = {p.name: p.read_text() for p in directory.glob('*Dockerfile*')}
        item = {'task': name, 'dockerfiles': {key: {
            'from_lines': [line.strip() for line in text.splitlines() if line.strip().upper().startswith('FROM ')],
            'mentions_ca_certificates': 'ca-certificates' in text,
            'ca_lines': [line.strip() for line in text.splitlines() if 'ca-certificates' in line],
        } for key, text in dockerfiles.items()}}
        matches = [image for image in images if image.startswith('tb4-prewarm/' + name + '-agent-main:')]
        if matches:
            item['image'] = matches[0]
            result = subprocess.run([
                'docker', 'run', '--rm', '--network', 'none', '--cpus', '0.25', '--memory', '128m',
                '-v', '/data/tb4/python/cpython-3.12.15-linux-x86_64-gnu:/tb4-runtime:ro',
                '--entrypoint', '/tb4-runtime/bin/python3.12', matches[0], '-c', CHECK,
            ], capture_output=True, text=True, timeout=45)
            item['inspection_exit'] = result.returncode
            if result.returncode == 0:
                item['ca_inspection'] = json.loads(result.stdout)
            else:
                item['error'] = result.stderr[-500:]
        records.append(item)
    report = {'network_requests': 0, 'selected_tasks': len(names),
              'available_images_checked': sum('image' in item for item in records), 'tasks': records}
    Path('/data/tb4/artifacts/skills-native-preflight/ca-image-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'selected': len(names), 'checked_images': report['available_images_checked'],
                      'items': [{'task': item['task'], 'inspection': item.get('ca_inspection'), 'error': item.get('error')}
                                for item in records if 'image' in item]}, indent=2))


if __name__ == '__main__':
    main()
