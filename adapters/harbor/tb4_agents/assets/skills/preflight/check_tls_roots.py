"""Test the managed runtime's TLS roots in representative task operating systems.

Only unauthenticated GET /v1/models is sent. No inference request or key is used.
"""

from pathlib import Path
import json
import subprocess
import certifi

CHECK = r'''import sys,ssl,http.client,json,pathlib,urllib.request,urllib.error
sys.path.insert(0,'/test-site')
import tb4_hook
before=ssl.create_default_context().cert_store_stats()
tb4_hook._install_tls_roots()
context=ssl._create_default_https_context()
assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname is True
explicit=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
explicit.check_hostname=False
connection=http.client.HTTPSConnection('localhost',context=explicit)
assert connection._context is explicit and explicit.check_hostname is False
try:
 with urllib.request.urlopen('https://yuzhiapi.xyz/v1/models',timeout=30) as response:status=response.status
except urllib.error.HTTPError as error:status=error.code
assert status==401,status
print(json.dumps({'os_release':pathlib.Path('/etc/os-release').read_text().splitlines()[:3],'before_store':before,'after_store':context.cert_store_stats(),'verify_mode':'CERT_REQUIRED','check_hostname':context.check_hostname,'explicit_context_preserved':True,'ssl_cert_file_env_changed':False,'unauthenticated_models_http_status':status,'inference_requests':0,'credentials_used':False}))'''


def main():
    images = subprocess.check_output(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], text=True).splitlines()
    ubuntu = next(image for image in images if image.startswith('tb4-prewarm/ks-solver-cpp-agent-main:'))
    report = {'representatives': {}, 'inference_requests': 0}
    for name, image in [('ubuntu24-without-ca', ubuntu), ('fedora42', 'fedora:42')]:
        result = subprocess.run([
            'docker', 'run', '--rm', '--read-only', '--cpus', '0.25', '--memory', '128m',
            '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-v', '/data/tb4/python/cpython-3.12.15-linux-x86_64-gnu:/tb4-runtime:ro',
            '-v', f"{Path(certifi.__file__).resolve().parent}:/test-site/certifi:ro",
            '-v', f"{Path(__file__).resolve().parents[1] / 'sitecustomize.py'}:/test-site/tb4_hook.py:ro",
            '--entrypoint', '/tb4-runtime/bin/python3.12', image, '-c', CHECK,
        ], capture_output=True, text=True, timeout=60, check=True)
        report['representatives'][name] = {'image': image, **json.loads(result.stdout)}
    Path('/data/tb4/artifacts/skills-native-preflight/tls-ca-compatibility.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
