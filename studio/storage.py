"""Blender scene storage (module 6): another team's S3 service, "storage" namespace.

The studio only speaks S3. Its two long-term keys (upload, links) stay in the MCP server, and serve one
purpose only: getting temporary credentials (STS AssumeRole), limited to the user's folder. The worker,
where the code written by the model runs, only receives those.

Module 7: the scene of a user signed in through the organization goes to their personal space
("spaces" bucket, personal/<Keycloak identifier>/ folder), with credentials in their name obtained by
identity.py; the studio has no key for this bucket.
"""
import hashlib
import hmac
import json
import re
import time
import unicodedata

import boto3
from botocore.config import Config

import common as c
import metrics as m

SETTINGS = Config(signature_version="s3v4", s3={"addressing_style": "path"},  # path, not subdomain
                  connect_timeout=3, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"},
                  request_checksum_calculation="when_required", response_checksum_validation="when_required")


def _client(service, address, key, secret, token=None):
    return boto3.client(service, endpoint_url=address, region_name=c.S3_REGION, aws_access_key_id=key,
                        aws_secret_access_key=secret, aws_session_token=token, config=SETTINGS)


def pseudonym(email: str) -> str:
    """One folder per user, without their address: the storage team has no need to see it."""
    return hmac.new(c.SECRET, email.encode(), hashlib.sha256).hexdigest()[:16]


def object_key(d: dict) -> str:
    return f"scenes/{pseudonym(d['user'])}/{d['id']}.blend"


def personal_key(sub: str, title: str, number: int) -> str:
    """Module 7: personal/<sub>/studio3d/<date>-<title>-<n>.blend. Storage enforces the folder
    (personal-space policy): any identifier other than the token's would be refused."""
    return f"personal/{sub}/studio3d/{time.strftime('%Y-%m-%d')}-{file_name({'title': title, 'number': number})}"


def credentials(key: str, secret: str, actions: list, resource: str, use: str) -> dict:
    """Swaps a long-term key for fifteen-minute credentials, limited to `resource`."""
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": actions, "Resource": [f"arn:aws:s3:::{c.BUCKET}/{resource}"]}]}
    r = _client("sts", c.S3_INTERNAL, key, secret).assume_role(
        RoleArn="arn:aws:iam::000000000000:role/studio3d", RoleSessionName="studio3d",
        DurationSeconds=c.CREDENTIALS_TTL, Policy=json.dumps(policy))["Credentials"]
    m.CREDENTIALS.labels(use=use).inc()
    return {"key": r["AccessKeyId"], "secret": r["SecretAccessKey"], "token": r["SessionToken"],
            "expires": r["Expiration"].timestamp()}


def prepare_upload(d: dict) -> dict:
    """MCP server: the right to write this one file, in the user's folder."""
    return credentials(c.S3_UPLOAD_KEY, c.S3_UPLOAD_SECRET, ["s3:PutObject"], object_key(d), "upload")


def file_name(d: dict) -> str:
    """Name suggested for the download: the title in ASCII, then the number (living-room-1.blend)."""
    title = unicodedata.normalize("NFKD", d.get("title", "scene")).encode("ascii", "ignore").decode()
    title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-") or "scene"
    return f"{title}-{d.get('number', '')}.blend"


def upload(path, d: dict, i: dict, creds: dict) -> dict:
    """Worker: uploads the scene with the temporary credentials prepared by the MCP server."""
    size = path.stat().st_size
    if size > c.SCENE_MAX_SIZE:
        raise ValueError("too_large")
    s3 = _client("s3", c.S3_INTERNAL, creds["key"], creds["secret"], creds["token"])
    # Module 7: personal space (bucket and key chosen by the MCP server during the call).
    bucket, key = (c.SPACES_BUCKET, d["scene_key"]) if d.get("personal") == "1" else (c.BUCKET, object_key(d))
    with path.open("rb") as f:
        rep = s3.put_object(
            Bucket=bucket, Key=key, Body=f, ContentLength=size,
            ContentType="application/x-blender",
            ContentDisposition=f'attachment; filename="{file_name(d)}"',
            Metadata={"digital-source-type": "trainedAlgorithmicMedia", "generator": "AI studio3D",
                      "id": f"urn:uuid:{i['id']}", "number": str(d.get("number", "")),
                      "assets": ",".join(a["name"] for a in i.get("assets", []))[:500]})
    return {"key": key, "version": rep.get("VersionId", ""), "bytes": size}


_read: dict = {}  # read credentials, by pseudonym (kept while they have two minutes left)


def presigned_link(d: dict) -> str:
    """MCP server: sixty-second link, signed for the PUBLIC address (the signature covers the host name),
    with read credentials limited to this user's folder."""
    pseudo = pseudonym(d["user"])
    creds = _read.get(pseudo)
    if not creds or creds["expires"] - time.time() < 120:
        creds = _read[pseudo] = credentials(c.S3_LINKS_KEY, c.S3_LINKS_SECRET,
                                            ["s3:GetObject", "s3:GetObjectVersion"], f"scenes/{pseudo}/*", "read")
    params = {"Bucket": c.BUCKET, "Key": d["scene"]}
    if d.get("scene_version"):
        params["VersionId"] = d["scene_version"]  # the version uploaded by the worker, not another one
    # Signing is a local computation: no network call to the public address.
    return _client("s3", c.S3_PUBLIC, creds["key"], creds["secret"], creds["token"]).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=c.S3_LINK_TTL)


def personal_presigned_link(read: dict, d: dict) -> str:
    """Module 7: ten-minute link to the scene in the personal space, signed for the PUBLIC address with
    the user's own read rights (this one file)."""
    params = {"Bucket": c.SPACES_BUCKET, "Key": d["scene"]}
    if d.get("scene_version"):
        params["VersionId"] = d["scene_version"]
    return _client("s3", c.S3_PUBLIC, read["key"], read["secret"], read["token"]).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=c.PERSONAL_LINK_TTL)
