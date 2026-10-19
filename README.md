# A personal storage space — carrying the user's identity all the way to storage

Code for module 7 of the “Click by Click” series **Building an agent-ready AI service: the 3D studio**
(also published in French as « Clic par clic » and in German as « Klick für Klick »).

Video: {{video}}

## Goal

In module 6, the 3D studio uploaded each Blender scene to another team's S3 storage, and the storage
took the studio at its word: the studio said whom it was uploading for, with its own long-term keys.
If the studio made a mistake, or was compromised, it could act for anyone.

In this module, **the organization vouches for the identity**, and that identity travels all the way
to storage:

1. **One identity provider** for the organization: **Keycloak 26.8.0**. The user signs in once.
2. **Open WebUI** signs in through Keycloak (OIDC) and passes the signed-in person's **access token**
   to the MCP server (connection authentication mode **“OAuth”**, `auth_type: system_oauth`).
3. **The MCP server verifies the token itself** (signature with Keycloak's public keys, issuer,
   audience `studio3d`, expiry) instead of trusting an `X-User-Email` header.
4. **It never passes that token on** (the MCP specification forbids it): it **exchanges** it with
   Keycloak (OAuth 2.0 Token Exchange, RFC 8693) for a token of the same person meant for storage
   (audience `storage`, five minutes).
5. **Storage verifies that token in turn** (`AssumeRoleWithWebIdentity`) and returns temporary
   credentials **in the user's name**, narrowed by a session policy to the single file to write.
6. **A single policy** for everyone, `personal-space`, enforces the folder from the identity read in
   the token: `spaces/personal/${aws:userid}/*`. One shared bucket, one folder per person; a newcomer
   gets their space as soon as they join the Keycloak group, with no administrator action.
7. **A second service**, the RustFS console, signs in with the same Keycloak: the user finds their
   scenes there; another user sees that the folder exists, and is refused by storage itself.
8. **The model's code leaves the worker**: the worker pod now has two containers. `worker` (Redis,
   upload) keeps the Redis password; `blender` (`launcher.py`, UID 10001, no secret, read-only root
   filesystem, no capability) runs the model's code. They only share a working folder. This fixes the
   limitation of module 6 (a hostile script could read the worker's environment and reach Redis, where
   the upload credentials of other requests wait).

What the studio can no longer do: upload or read in the personal space of someone who is not calling
it. It has no key for the `spaces` bucket, and its Keycloak client secret yields no user token.

## Contents

| Path | Role |
|---|---|
| `identity/realm.json` | Keycloak realm `example-org`: group `personal-space`; users; clients `open-webui` (its tokens are also addressed to `studio3d`), `studio3d` (standard token exchange enabled, no service account, exchanged tokens valid five minutes), `storage` (console, PKCE), `test` and `test-studio` (direct grant, **recording realm only**); `groups` claim without the full path |
| `identity/k8s.yaml` | Keycloak 26.8.0 (`start-dev`, realm imported at startup), service, Ingress `sso.localhost`, ingress rule |
| `identity/open-webui.env` | Open WebUI variables: internal discovery address, client, sign-up off, merge with existing accounts by email |
| `identity/deploy.sh` | Secrets (`identity/.env`), realm, deployment; `--plug-open-webui`, `--unplug-open-webui`, `--token` |
| `storage/k8s.yaml` | RustFS 1.0.1 with the OIDC variables (`RUSTFS_IDENTITY_OPENID_*`, `RUSTFS_OUTBOUND_ALLOW_ORIGINS`), an init container that waits for Keycloak, ingress rule, public read-only route, console |
| `storage/policies/personal-space.json` | The single policy of the personal spaces (`${aws:userid}` in the resource and in the listing condition) |
| `storage/policies/upload.json`, `links.json`, `lifecycle.json` | Module 6: studio accounts usable only through temporary credentials, bucket lifecycle |
| `storage/prepare/`, `storage/prepare.yaml`, `storage/deploy.sh` | Preparation image and Job (`prepare.py`: buckets, policies, accounts; `--identity-test`), deployment script (`--keys`, `--plug-identity`, `--identity-test`, `--empty`) |
| `studio/identity.py` | `verify` (PyJWT, public keys, issuer, audience), `exchange` (RFC 8693), `rights` (`AssumeRoleWithWebIdentity` + session policy) |
| `studio/api.py` | `_user` (organization token, otherwise the module 6 shared key), `_upload_rights` (exchange and rights **during the call**), `render_status` (ten-minute link signed with the user's own read rights) |
| `studio/storage.py` | Personal key `personal/<sub>/studio3d/<date>-<title>-<n>.blend`, upload, presigned links |
| `studio/worker.py`, `studio/launcher.py` | The worker's agent and its `blender` container: jobs handed over through the shared folder |
| `studio/render.py`, `package.py`, `marking.py`, `metrics.py`, `common.py`, `Dockerfile` | The rest of the studio (modules 1 to 6), image `studio3d-en:7` |
| `k8s-scale.yaml` | Redis, MCP server ×2, workers 0 to 4 (KEDA) with two containers, Ingress, network rules |
| `k8s-storage.yaml`, `k8s-identity.yaml` | Studio egress rules: to the `s3` service (all studio pods), to Keycloak (MCP server only) |
| `open-webui/` | “Studio 3D” connection in OAuth mode, system prompt of the “Studio 3D” model |
| `test/hostile-container.py` | A hostile “scene”: looks for secrets in `/proc`, tries Redis and storage |

## Prerequisites

- The studio of **module 5** for the asset library image `studio3d-assets:1` (repository
  [`clic-par-clic-studio3d-05-assets`](https://github.com/guilhem-/clic-par-clic-studio3d-05-assets),
  which builds it with its `catalog.json`), KEDA 2.21.0, and the storage of **module 6**
  (repository [`clic-par-clic-studio3d-06-stockage-s3`](https://github.com/guilhem-/clic-par-clic-studio3d-06-stockage-s3)) —
  this repository contains the storage again, with its module 7 additions.
- A **k3d** cluster (k3s 1.35, Traefik published on port 8080: the addresses `sso.localhost:8080`,
  `s3.localhost:8080`, `stockage.localhost:8080`, `studio.localhost:8080` are used as is), `kubectl`,
  Docker, `openssl`, `curl`.
- **Open WebUI 0.11.3** (the “OAuth” mode of tool server connections, and the fix of issue #24618,
  are needed). About 1 GiB of free memory for Keycloak.

## Step-by-step installation

1. **Identity** (identity team):

   ```bash
   ./identity/deploy.sh          # Keycloak, realm example-org, http://sso.localhost:8080
   ```

   Check that the issuer is the same from your machine and from a pod: the discovery document says
   `"issuer": "http://sso.localhost:8080/realms/example-org"` in both cases, while `token_endpoint`
   and `jwks_uri` use the internal address when read from a pod (`KC_HOSTNAME_BACKCHANNEL_DYNAMIC`).

2. **Storage** (storage team):

   ```bash
   ./storage/deploy.sh                  # RustFS, buckets studio3d-scenes and spaces, policies, accounts
   ./storage/deploy.sh --keys           # module 6 keys handed to the studio ("storage" secret)
   ./storage/deploy.sh --plug-identity  # RustFS verifies Keycloak tokens
   ./storage/deploy.sh --identity-test  # real tokens: each "ok" line is an expected result
   ./storage/deploy.sh --identity-test oliver lukas   # the same test with two other users of the realm
   ```

3. **The studio**:

   ```bash
   docker build -t studio3d-en:7 studio && k3d image import studio3d-en:7 -c tterm
   kubectl create namespace studio3d
   kubectl create secret generic studio -n studio3d --from-literal=token="$(openssl rand -hex 24)" \
     --from-literal=redis="$(openssl rand -hex 24)"
   kubectl create secret generic identity -n studio3d \
     --from-literal=client-secret="$(grep '^SECRET_STUDIO3D=' identity/.env | cut -d= -f2-)"
   kubectl apply -f k8s-scale.yaml -f k8s-storage.yaml -f k8s-identity.yaml
   ```

   `./storage/deploy.sh --keys` needs the `studio3d` namespace: run it again after this step if it
   complained in step 2, then `kubectl -n studio3d rollout restart deployment/studio-api`.

4. **Open WebUI**:

   ```bash
   OWUI_NAMESPACE=open-webui OWUI_DEPLOYMENT=open-webui ./identity/deploy.sh --plug-open-webui
   ```

   The sign-in page shows “Continue with Example Organization”. In *Admin Settings › Integrations*,
   edit the “Studio 3D” connection: **Auth: OAuth** (“Forwards system user OAuth access token to
   authenticate”), remove the `X-User-Email` header, save (`open-webui/connection.json`). Use
   `open-webui/system-prompt.txt` as the system prompt of the “Studio 3D” model.

5. **Try it**: sign in to Open WebUI with **Continue with Example Organization** (an existing account
   with the same email is linked, and keeps its conversations), ask “Create an office scene with the
   library's metal desk, wooden chair and lamp, under the bright loft lighting.”, then “How is my
   render doing?”. The answer says the scene is stored in your personal space, with a ten-minute
   download link. Open `http://stockage.localhost:8080/rustfs/console/`, click **Sign in with Example
   Organization**: no password, your folder `spaces/personal/<your id>/studio3d/`. Sign in as another
   user and open that folder: access denied.

6. **The hostile container**: submit `test/hostile-container.py` as the code of a scene (for instance
   with the MCP inspector); the error returned by `render_status` lists what it found: only `blender`
   and `python` processes, no secret, `NOAUTH` from Redis, `AccessDenied` from storage.

## Things to know

- **One issuer, two addresses.** Every token carries its issuer's address. The browser reaches
  Keycloak at `sso.localhost`, the pods at `keycloak.identity.svc`: Keycloak always signs with the
  public address (`KC_HOSTNAME`) and answers the pods at the internal one
  (`KC_HOSTNAME_BACKCHANNEL_DYNAMIC`). Every verifier checks the public issuer.
- **Audiences.** RustFS refuses a token whose `aud` contains an audience it does not trust: the token
  presented to storage must be meant for storage alone — which is exactly what the exchange produces.
- **Durations.** RustFS honors `DurationSeconds=1800` even though the exchanged token expires in five
  minutes: the upload credentials outlive the token, which is never stored.
- **`${aws:userid}`** is the token's `sub` (the Keycloak identifier, an opaque value). RustFS replaces
  `aws:*` variables in resources and conditions; `jwt:sub` is only a condition key.
- **The console** needs `s3:ListBucket` at the root of the bucket (no prefix, with the `/` delimiter)
  and `s3:GetBucketVersioning`: the folder names under `personal/` are visible to the group's
  members, never their contents. A listing of the whole bucket without a delimiter is refused.
- **RustFS reads the OIDC discovery only at startup.** On a fresh pod, Kubernetes network rules can
  take about a second to let it in: the `wait-for-identity` init container avoids a provider that
  stays silently unavailable. Without `RUSTFS_OUTBOUND_ALLOW_ORIGINS`, the discovery fails as well.
- **Open WebUI refreshes the token** when less than five minutes remain: keep the access token
  lifespan above that (ten minutes here). A user signed in with a local password has no token: the
  studio answers “sign in with the organization account”.
- **Other integrations** (functions, widgets) that call the studio with its shared key keep the
  module 6 behavior: their scenes go to `studio3d-scenes`.

## In production

- HTTPS everywhere (OAuth 2.1, secure cookies, console).
- The organization's existing provider (Entra ID, Okta, Keycloak…), in production mode (database,
  several copies, strong authentication); no `test` clients. With Entra ID, the exchange is called
  “On-Behalf-Of”.
- For an MCP server shared by several clients: the “OAuth 2.1” mode, a token for the studio alone
  (RFC 9728 metadata, RFC 8707 `resource`); a “downscope only” policy on the exchange.
- A stronger sandbox for the model's code (gVisor, Kata Containers), dedicated nodes.
- Upload credentials still wait in Redis (thirty minutes, one file, one person): readable by whoever
  administers Redis, and a temporary credential cannot be revoked before it expires.

## Sources

- Keycloak: [token exchange](https://www.keycloak.org/securing-apps/token-exchange),
  [hostname configuration](https://www.keycloak.org/server/hostname),
  [importing a realm](https://www.keycloak.org/server/importExport).
- RustFS: [OIDC with Keycloak](https://docs.rustfs.com/en/security-compliance/oidc/keycloak),
  [STS](https://docs.rustfs.com/en/security-compliance/iam/sts).
- Open WebUI: [MCP](https://docs.openwebui.com/features/extensibility/mcp/),
  [SSO](https://docs.openwebui.com/features/authentication-access/auth/sso/).
- [MCP specification 2025-11-25, authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
  (“Access Token Privilege Restriction”).
- [RFC 8693, OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693).
- [PyJWT](https://pyjwt.readthedocs.io/), [boto3 `assume_role_with_web_identity`](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sts/client/assume_role_with_web_identity.html).

## License

Code: MIT, see [`LICENSE`](LICENSE). Keycloak, RustFS, Open WebUI, Blender, Redis and KEDA are
independent projects; this series is independent of the publishers of the software shown.

## The series

- Previous module: **Downloading the Blender scene from another team's storage** — repository
  [`clic-par-clic-studio3d-06-stockage-s3`](https://github.com/guilhem-/clic-par-clic-studio3d-06-stockage-s3).
- This is the last module of the series. Going further: the **AI Integrations** series connects this
  studio to Open WebUI in four different ways.
