"""Storage preparation by the storage team (modules 6 and 7). Idempotent: safe to run again.

  python prepare.py                    buckets, versioning, lifecycle, policies, accounts, final check
  python prepare.py --empty            empties scenes/ and personal/ (all versions)
  python prepare.py --identity-test    module 7: organization tokens, personal spaces (expected refusals)

Run by a Kubernetes Job in the "storage" namespace, with the root key (s3-root secret) and the account
secrets (accounts secret). Files read in /config: policies/*.json, lifecycle.json.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

S3 = os.environ.get("S3_INTERNAL", "http://s3.storage.svc.cluster.local:9000")
BUCKET = os.environ.get("BUCKET", "studio3d-scenes")
# Module 7: a shared bucket, one folder per person (personal/<Keycloak identifier>/), enforced by a single
# policy; it carries the name of the Keycloak group that grants it.
SPACES = os.environ.get("SPACES_BUCKET", "spaces")
SPACES_POLICY = "personal-space"
CONFIG = Path(os.environ.get("CONFIG", "/config"))
SETTINGS = Config(signature_version="s3v4", s3={"addressing_style": "path"},
                  request_checksum_calculation="when_required", response_checksum_validation="when_required")
# Module 6: two accounts, two policies; the root key is never handed to the studio.
ACCOUNTS = {"studio3d-upload": "UPLOAD", "studio3d-links": "LINKS"}


def client(key, secret, token=None, service="s3"):
    return boto3.client(service, endpoint_url=S3, region_name="us-east-1", aws_access_key_id=key,
                        aws_secret_access_key=secret, aws_session_token=token, config=SETTINGS)


def root():
    return client(os.environ["ROOT_KEY"], os.environ["ROOT_SECRET"])


def rc(*args):
    """RustFS rc client (accounts and policies); the output is summed up in one line."""
    p = subprocess.run(["rc", "--no-color", *args], capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode, (out[-1] if out else "")


def step(text):
    print(f"  {text}", flush=True)


def wait():
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{S3}/health", timeout=2):
                return
        except Exception:
            time.sleep(2)
    raise SystemExit("the s3 service does not answer")


def bucket(s3, name):
    try:
        s3.head_bucket(Bucket=name)
        step(f"bucket {name}: already there")
    except ClientError:
        s3.create_bucket(Bucket=name)
        step(f"bucket {name}: created")
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})


def prepare():
    s3 = root()
    # Module 6: the studio's bucket, versioned (each link points to its version), with a lifecycle.
    bucket(s3, BUCKET)
    rules = json.loads((CONFIG / "lifecycle.json").read_text(encoding="utf-8"))
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration=rules)
    step("lifecycle: " + ", ".join(r["ID"] for r in rules["Rules"]))
    rc("alias", "set", "root", S3, os.environ["ROOT_KEY"], os.environ["ROOT_SECRET"])
    for account, prefix in ACCOUNTS.items():
        policy = CONFIG / "policies" / f"{account.removeprefix('studio3d-')}.json"
        code, msg = rc("admin", "policy", "create", "root", account, str(policy))
        assert code == 0, msg
        code, msg = rc("admin", "user", "add", "root", account, os.environ[f"{prefix}_SECRET"])
        assert code == 0, msg
        code, msg = rc("admin", "policy", "attach", "root", account, "--user", account)
        assert code == 0 or "already" in msg.lower(), msg
        step(f"account {account}: policy {account}")
    # Module 7: the personal spaces bucket (versioned, no lifecycle: everyone tidies up their own) and
    # the single policy, matched by RustFS to the Keycloak group of the same name.
    bucket(s3, SPACES)
    code, msg = rc("admin", "policy", "create", "root", SPACES_POLICY, str(CONFIG / "policies" / f"{SPACES_POLICY}.json"))
    assert code == 0, msg
    step(f"policy {SPACES_POLICY}: {SPACES}/personal/${{aws:userid}}/*")


def empty(name=BUCKET, prefix="scenes/", verbose=True):
    s3 = root()
    try:
        s3.head_bucket(Bucket=name)
    except ClientError:
        return
    n = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=name, Prefix=prefix):
        objects = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=name, Delete={"Objects": objects, "Quiet": True})
            n += len(objects)
    if verbose:
        step(f"{name}/{prefix} emptied: {n} versions deleted")


def identity_test():
    """Module 7: real organization tokens (two users, A and B), presented to storage as the studio
    would. The folder is enforced by storage, from the identity read in the token."""
    na, nb = os.environ.get("NAME_A", "Camille"), os.environ.get("NAME_B", "Lukas")

    def attempt(label, f, expected):
        try:
            f()
            got = "accepted"
        except ClientError as e:
            got = "refused" if e.response["Error"]["Code"] in ("AccessDenied", "403") else e.response["Error"]["Code"]
        print(f"  {'ok ' if got == expected else 'KO '} {label}: {got}", flush=True)
        return got == expected

    def rights(token, policy=None, duration=900):
        """AssumeRoleWithWebIdentity: RustFS verifies the token itself (signature, issuer, audience)."""
        sts = boto3.client("sts", endpoint_url=S3, region_name="us-east-1", config=SETTINGS)
        extra = {"Policy": json.dumps(policy)} if policy else {}
        r = sts.assume_role_with_web_identity(RoleArn="arn:aws:iam::000000000000:role/personal-space",
                                              RoleSessionName="test", WebIdentityToken=token, DurationSeconds=duration, **extra)
        c = r["Credentials"]
        return client(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"]), c["Expiration"]

    def claims(token):
        body = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))

    ta, tb = os.environ["TOKEN_A"], os.environ["TOKEN_B"]
    sa, sb = claims(ta)["sub"], claims(tb)["sub"]
    a, end = rights(ta, duration=1800)  # thirty minutes requested, while the token expires earlier
    b, _ = rights(tb)
    at_a, at_b = f"personal/{sa}/studio3d/test.blend", f"personal/{sb}/studio3d/test.blend"
    one_file, _ = rights(ta, {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:PutObject"],
                                                                     "Resource": [f"arn:aws:s3:::{SPACES}/{at_a}"]}]})
    upload = client("studio3d-upload", os.environ["UPLOAD_SECRET"])
    c = client("studio3d-upload", os.environ["UPLOAD_SECRET"], service="sts").assume_role(
        RoleArn="arn:aws:iam::000000000000:role/studio3d", RoleSessionName="test", DurationSeconds=900)["Credentials"]
    upload_temp = client(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
    minutes = round((end.timestamp() - time.time()) / 60)
    left = round((claims(ta)["exp"] - time.time()) / 60)
    print(f"  {'ok ' if 28 <= minutes <= 30 else 'KO '} {na}'s credentials: {minutes} min (token: {left} min)", flush=True)
    results = [
        attempt(f"{na}'s rights, own folder", lambda: a.put_object(Bucket=SPACES, Key=at_a, Body=b"x"), "accepted"),
        attempt(f"{na}'s rights, list personal/", lambda: a.list_objects_v2(Bucket=SPACES, Prefix="personal/", Delimiter="/"), "accepted"),
        attempt(f"{na}'s rights, list own folder", lambda: a.list_objects_v2(Bucket=SPACES, Prefix=f"personal/{sa}/", Delimiter="/"), "accepted"),
        attempt(f"{na}'s rights, whole bucket at once", lambda: a.list_objects_v2(Bucket=SPACES), "refused"),
        attempt(f"{na}'s rights, {nb}'s folder", lambda: a.put_object(Bucket=SPACES, Key=at_b, Body=b"x"), "refused"),
        attempt(f"{na}'s rights, list {nb}'s folder", lambda: a.list_objects_v2(Bucket=SPACES, Prefix=f"personal/{sb}/"), "refused"),
        attempt(f"{nb}'s rights, {na}'s scene", lambda: b.get_object(Bucket=SPACES, Key=at_a), "refused"),
        attempt(f"{na}'s single-file rights, another file", lambda: one_file.put_object(Bucket=SPACES, Key=f"personal/{sa}/studio3d/other.blend", Body=b"x"), "refused"),
        attempt("studio key (module six), spaces", lambda: upload.put_object(Bucket=SPACES, Key=at_a, Body=b"x"), "refused"),
        attempt("studio credentials (module six), spaces", lambda: upload_temp.put_object(Bucket=SPACES, Key=at_a, Body=b"x"), "refused"),
    ]
    empty(SPACES, f"personal/{sa}/studio3d/test", verbose=False)
    if not all(results) or not 28 <= minutes <= 30:
        raise SystemExit("identity: failed")
    print("  identity: all as expected", flush=True)


if __name__ == "__main__":
    wait()
    if "--empty" in sys.argv:
        empty()
        empty(SPACES, "personal/")
    elif "--identity-test" in sys.argv:
        identity_test()
    else:
        prepare()
