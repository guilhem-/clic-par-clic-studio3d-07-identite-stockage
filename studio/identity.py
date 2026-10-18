"""User identity, vouched for by the organization (module 7).

Open WebUI passes the signed-in person's access token to the studio ("OAuth" mode of the MCP
connection). The studio no longer takes anyone at their word: it verifies this token itself, then, for
storage, exchanges it with Keycloak (RFC 8693) for a token of the same person meant for storage. It never
passes on the token it received, and stores it nowhere.
"""
import json
import urllib.parse
import urllib.request

import boto3
import jwt

import common as c

# Keycloak's public keys, read from the internal address, cached (refreshed when a key changes).
_keys = jwt.PyJWKClient(f"{c.OIDC_INTERNAL}/protocol/openid-connect/certs", cache_keys=True, lifespan=600)


def verify(token: str) -> dict:
    """Is the token signed by Keycloak, issued by the organization, addressed to the studio, still valid?"""
    key = _keys.get_signing_key_from_jwt(token)
    r = jwt.decode(token, key.key, algorithms=["RS256"],
                   issuer=c.OIDC_ISSUER,    # the public address, the same for everyone
                   audience="studio3d",     # a token made for another service is refused
                   leeway=30, options={"require": ["exp", "iss", "aud", "sub"]})
    return {"sub": r["sub"], "email": r.get("email", "").lower(), "groups": r.get("groups", [])}


def exchange(token: str) -> str:
    """Token exchange (RFC 8693): the person's token, for the studio, against a token of the same
    person for storage, valid five minutes. The studio authenticates with its client secret."""
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": "storage",
        "client_id": "studio3d", "client_secret": c.OIDC_CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(f"{c.OIDC_INTERNAL}/protocol/openid-connect/token", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)["access_token"]


def rights(storage_token: str, actions: list, key: str, duration: int) -> dict:
    """Storage verifies the token in turn (AssumeRoleWithWebIdentity) and returns credentials in the
    person's name; the session policy narrows them down to this one file."""
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": actions, "Resource": [f"arn:aws:s3:::{c.SPACES_BUCKET}/{key}"]}]}
    sts = boto3.client("sts", endpoint_url=c.S3_INTERNAL, region_name=c.S3_REGION)
    r = sts.assume_role_with_web_identity(
        RoleArn="arn:aws:iam::000000000000:role/personal-space",  # required by boto3, ignored by RustFS
        RoleSessionName="studio3d", WebIdentityToken=storage_token,
        DurationSeconds=duration, Policy=json.dumps(policy))["Credentials"]
    return {"key": r["AccessKeyId"], "secret": r["SecretAccessKey"], "token": r["SessionToken"],
            "expires": r["Expiration"].timestamp()}
