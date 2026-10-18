"""The worker's "blender" container (module 7): the code written by the model runs here, without any secret.

It talks to no one. It waits for a job in the folder shared with the worker's agent ("work" volume),
runs Blender (render, then scene packaging), writes "done", and waits for the next one.
No Redis import, no key, no token: a hostile script finds nothing to steal here.
"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

WORK_DIR = Path(os.environ.get("WORK_DIR", "/work"))
BLENDER = os.environ.get("BLENDER", "/opt/blender/blender")
HERE = Path(__file__).parent
# Requested step -> Blender arguments (the job folder last).
STEPS = {
    "render": lambda d: ["-b", "--factory-startup", "--python", str(HERE / "render.py"), "--", str(d)],
    "package": lambda d: ["-b", "--factory-startup", "--disable-autoexec", str(d / "raw.blend"),
                          "--python", str(HERE / "package.py"), "--", str(d)],
}


def run(d: Path) -> None:
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "ASSETS": os.environ.get("ASSETS", "/assets")}
    if job.get("scene"):
        env["SCENE_BLEND"] = "1"
    try:
        p = subprocess.run([BLENDER, *STEPS[job["step"]](d)], capture_output=True, text=True,
                           timeout=job.get("timeout", 300), env=env)
        (d / "output.txt").write_text(p.stdout[-20000:], encoding="utf-8")
        end = str(p.returncode)
    except subprocess.TimeoutExpired:
        end = "timeout"
    (d / "done").write_text(end)  # last: the worker's agent may read the files


def main() -> None:
    os.umask(0o002)  # files readable and deletable by the worker's agent (same group)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))  # pod stopping: finish the current request
    print("blender ready", flush=True)
    while not (stop and not any(WORK_DIR.iterdir())):  # the agent deletes a finished request's folder
        for d in sorted(WORK_DIR.iterdir()) if WORK_DIR.exists() else []:
            if (d / "job.json").exists() and not (d / "taken").exists():
                (d / "taken").touch()
                run(d)
        time.sleep(0.3)


if __name__ == "__main__":
    main()
