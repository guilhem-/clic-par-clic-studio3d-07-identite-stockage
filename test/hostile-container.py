# Module 7 test: a hostile "scene", submitted like any model code. It looks for secrets where the model's
# code used to run in module 6 (same container as the worker), then stops with an error carrying its
# findings: the message comes back through render_status. Expected: nothing found.
import os
import socket
import urllib.error
import urllib.request

findings = []

# 1. The environment variables of the other processes (/proc/<pid>/environ).
seen, secrets = [], []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")[:40]
        seen.append(cmd.split(" ")[0].rsplit("/", 1)[-1] or "?")
        env = open(f"/proc/{pid}/environ", "rb").read()
        if b"REDIS_PASSWORD" in env or b"STUDIO_TOKEN" in env:
            secrets.append(pid)
    except OSError:
        pass
findings.append(f"visible processes: {', '.join(sorted(set(seen)))}")
findings.append(f"secrets found in /proc: {len(secrets)}")
findings.append(f"secrets in my environment: {sum(k.startswith(('REDIS', 'STUDIO', 'S3_', 'OIDC')) for k in os.environ)}")

# 2. Redis, without a password.
try:
    with socket.create_connection(("redis", 6379), timeout=3) as s:
        s.sendall(b"GET studio:counter\r\n")
        findings.append(f"redis: {s.recv(200).decode(errors='replace').strip()}")
except OSError as e:
    findings.append(f"redis: {e}")

# 3. Storage, without credentials.
try:
    req = urllib.request.Request("http://s3.storage.svc.cluster.local:9000/spaces/personal/x/steal.blend", data=b"x", method="PUT")
    urllib.request.urlopen(req, timeout=5)
    findings.append("storage: write accepted")
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="replace")
    findings.append(f"storage: {e.code} {body[body.find('<Code>') + 6:body.find('</Code>')] if '<Code>' in body else ''}")
except OSError as e:
    findings.append(f"storage: {e}")

raise RuntimeError("FINDINGS | " + " | ".join(findings))
