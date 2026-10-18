"""3D studio at scale: what the MCP server (api.py) and the workers (worker.py) share.

All the state lives in Redis, so that the server runs as several stateless copies and the workers
fetch the requests themselves:

  studio:counter               number of the next request (INCR)
  studio:request:<n>           hash: title, code, user, status, submitted, started, finished, error, worker,
                               id (UUID, traceability), visible_marking ("1" or "0"), assets, library,
                               scene_planned, scene (S3 object key), scene_version, scene_bytes, scene_status,
                               personal ("1": scene in the user's personal space), sub, scene_key (module 7)
  studio:queue                 requests waiting (LPUSH on the left, taken from the right)
  studio:processing            requests taken by a worker (BLMOVE queue -> processing)
  studio:load                  one entry per request waiting or running: what KEDA measures
  studio:alive:<n>             heartbeat of the worker handling <n> (expires in 10 s)
  studio:file:<n>:<ext>        PNG and GLB produced (expire in 24 h)
  studio:upload:<n>            temporary upload credentials for the scene of <n> (module 6: 15 min;
                               module 7: in the user's name, 30 min, obtained during their call)
  studio:users:<hour>          distinct users of the hour (HyperLogLog, expires in 2 h; only the count is published)
"""
import hashlib
import hmac
import json
import os
from functools import lru_cache
from pathlib import Path

import redis

TOKEN = os.environ.get("STUDIO_TOKEN", "")
SECRET = os.environ.get("STUDIO_SECRET", TOKEN).encode()
PUBLIC = os.environ.get("STUDIO_PUBLIC_URL", "http://studio.localhost:8080").rstrip("/")
FILE_TTL = int(os.environ.get("FILE_TTL", str(24 * 3600)))

# Marking of the renders (module 4): the organization's policy, read from the environment (yes / no).
#   MARKING_VISIBLE_DEFAULT: visible banner when the user does not say
#   MARKING_VISIBLE_ENFORCED: visible banner always, whatever the user asks
# The invisible marking (metadata) is always applied.
_yes = lambda name: os.environ.get(name, "no").strip().lower() in ("yes", "1", "true", "oui")
MARKING_DEFAULT = _yes("MARKING_VISIBLE_DEFAULT")
MARKING_ENFORCED = _yes("MARKING_VISIBLE_ENFORCED")

# Asset library (module 5): an image mounted read-only on this folder. Its catalog.json is built by the
# module 5 repository (models, environments; name, file, thumbnail, tags, source of each asset).
ASSETS = Path(os.environ.get("ASSETS", "/assets"))
LANGUAGE = os.environ.get("STUDIO_LANGUAGE", "EN")  # language of the labels returned to the model

# Scene storage (module 6): another team's S3 service. Optional: without the keys (no "storage" secret),
# the studio behaves as in the previous modules.
S3_INTERNAL = os.environ.get("S3_INTERNAL", "")    # address seen from the pods (uploads, temporary credentials)
S3_PUBLIC = os.environ.get("S3_PUBLIC", "")        # address seen from the browser (presigned links)
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
BUCKET = os.environ.get("S3_BUCKET", "studio3d-scenes")
S3_UPLOAD_KEY, S3_UPLOAD_SECRET = os.environ.get("S3_UPLOAD_KEY", ""), os.environ.get("S3_UPLOAD_SECRET", "")
S3_LINKS_KEY, S3_LINKS_SECRET = os.environ.get("S3_LINKS_KEY", ""), os.environ.get("S3_LINKS_SECRET", "")
STORAGE = bool(S3_INTERNAL and S3_PUBLIC and S3_UPLOAD_KEY and S3_LINKS_KEY)  # MCP server: keys received
SCENE_TTL = int(os.environ.get("SCENE_TTL", str(7 * 24 * 3600)))           # like the bucket lifecycle
S3_LINK_TTL = int(os.environ.get("S3_LINK_TTL", "60"))                       # presigned link: one minute
CREDENTIALS_TTL = 900                                                        # STS minimum: fifteen minutes
SCENE_MAX_SIZE = int(os.environ.get("SCENE_MAX_SIZE", str(200 * 1024 * 1024)))

# Organization identity (module 7): Keycloak. Optional: without the "identity" secret, the studio behaves
# as in module 6 (shared key and Open WebUI header).
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")                 # public address: the one written in the tokens
OIDC_INTERNAL = os.environ.get("OIDC_INTERNAL", "").rstrip("/")  # address seen from the pods (public keys, exchange)
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")    # the studio is a Keycloak client
OIDC = bool(OIDC_ISSUER and OIDC_INTERNAL and OIDC_CLIENT_SECRET and S3_INTERNAL and S3_PUBLIC)
SPACES_BUCKET = os.environ.get("S3_SPACES_BUCKET", "spaces")    # one folder per person: personal/<sub>/
PERSONAL_UPLOAD_TTL = 1800     # upload credentials in the user's name: time to wait and render
PERSONAL_READ_TTL = 900
PERSONAL_LINK_TTL = 600        # presigned link to the scene: ten minutes
SCENE_LABEL = {"FR": "Télécharger la scène Blender", "EN": "Download the Blender scene",
               "DE": "Blender-Szene herunterladen"}.get(LANGUAGE, "Download the Blender scene")
SPACE_LABEL = {"FR": "dans votre espace personnel", "EN": "in your personal space",
               "DE": "in Ihrem persönlichen Bereich"}.get(LANGUAGE, "in your personal space")


@lru_cache(maxsize=1)
def catalog() -> dict:
    """Catalog of the mounted library (read once: a pod keeps the version mounted when it started)."""
    sheet = ASSETS / "catalog.json"
    if not sheet.exists():
        return {"version": None, "models": {}, "environments": {}}
    return json.loads(sheet.read_text(encoding="utf-8"))


def asset_trace(names) -> list[dict]:
    """What the studio keeps about each asset used: name, source, license (catalog names only)."""
    cat = catalog()
    sheets = {**cat.get("models", {}), **cat.get("environments", {})}
    return [{"name": n, "source": f"Poly Haven {sheets[n]['source']['polyhaven']}" if sheets[n]["source"].get("polyhaven")
             else sheets[n]["source"].get("origin", "organization"), "license": sheets[n]["source"].get("license", "")}
            for n in names if n in sheets]


QUEUE, PROCESSING, LOAD = "studio:queue", "studio:processing", "studio:load"
request = lambda n: f"studio:request:{n}"
alive = lambda n: f"studio:alive:{n}"
file = lambda n, ext: f"studio:file:{n}:{ext}"
upload_s3 = lambda n: f"studio:upload:{n}"


def connection() -> redis.Redis:
    return redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), port=6379,
                       password=os.environ.get("REDIS_PASSWORD") or None, decode_responses=False)


def text(value) -> str:
    return value.decode() if isinstance(value, bytes) else (value or "")


def signature(number: int, extension: str) -> str:
    return hmac.new(SECRET, f"{number}.{extension}".encode(), hashlib.sha256).hexdigest()[:10]


def link(number: int, extension: str) -> str:
    return f"{PUBLIC}/r/{number}/{signature(number, extension)}.{extension}"


def artifact(number: int, title: str) -> str:
    return (
        '```html\n'
        '<script type="module" src="https://cdn.jsdelivr.net/npm/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>\n'
        f'<model-viewer src="{link(number, "glb")}" poster="{link(number, "png")}" alt="{title}" '
        'camera-controls auto-rotate shadow-intensity="1" '
        'style="width:100%;height:480px;background:#f4f4f5"></model-viewer>\n'
        '```'
    )
