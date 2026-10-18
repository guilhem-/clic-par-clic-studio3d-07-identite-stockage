#!/usr/bin/env bash
# The storage team's S3 storage (modules 6 and 7): RustFS 1.0.1 in the "storage" namespace, then
# preparation (buckets, versioning, lifecycle, policies, accounts).
#   ./storage/deploy.sh                    deploys and prepares (idempotent)
#   ./storage/deploy.sh --keys             hands the upload and links keys to the studio ("storage" secret)
#   ./storage/deploy.sh --plug-identity    RustFS verifies Keycloak tokens (s3-oidc secret, restart)
#   ./storage/deploy.sh --identity-test    real tokens of two users (Camille, Lukas): expected refusals
#   ./storage/deploy.sh --empty            empties scenes/ and personal/ (all versions)
# Secrets in storage/.env (created on first run, never committed). Requires docker, kubectl, k3d (or a
# registry: replace "k3d image import" with a docker push), openssl; identity/.env for --plug-identity.
set -euo pipefail
cd "$(dirname "$0")"
ENV=.env
[ -f "$ENV" ] || {
  echo "S3_ROOT_KEY=$(openssl rand -hex 10)" > "$ENV"
  for n in ROOT UPLOAD LINKS; do echo "S3_${n}_SECRET=$(openssl rand -hex 20)" >> "$ENV"; done
}
set -a; . "./$ENV"; set +a
CLUSTER=${K3D_CLUSTER:-tterm}

job() {  # runs the prepare Job with the given mode, waits for it, prints its output
  kubectl delete job prepare -n storage --ignore-not-found
  sed "s/\"MODE\"/\"$1\"/" prepare.yaml | kubectl apply -f -
  kubectl wait --for=condition=complete job/prepare -n storage --timeout=180s || true
  kubectl logs job/prepare -n storage
}

case "${1:-}" in
  --empty) job --empty; exit ;;
  --keys)
    kubectl create secret generic storage -n studio3d \
      --from-literal=upload-key=studio3d-upload --from-literal=upload-secret="$S3_UPLOAD_SECRET" \
      --from-literal=links-key=studio3d-links --from-literal=links-secret="$S3_LINKS_SECRET" \
      --dry-run=client -o yaml | kubectl apply -f -
    exit ;;
  --plug-identity)
    SECRET_STORAGE=$(grep '^SECRET_STORAGE=' ../identity/.env | cut -d= -f2-)
    kubectl create secret generic s3-oidc -n storage --from-literal=enabled=on \
      --from-literal=client-secret="$SECRET_STORAGE" --dry-run=client -o yaml | kubectl apply -f -
    kubectl rollout restart deployment/s3 -n storage   # the OIDC discovery is read at startup
    kubectl rollout status deployment/s3 -n storage --timeout=180s
    exit ;;
  --identity-test)
    kubectl create secret generic test-tokens -n storage \
      --from-literal=a="$(../identity/deploy.sh --token camille)" --from-literal=b="$(../identity/deploy.sh --token lukas)" \
      --from-literal=name-a=Camille --from-literal=name-b=Lukas --dry-run=client -o yaml | kubectl apply -f -
    job --identity-test
    kubectl delete secret test-tokens -n storage
    exit ;;
esac

docker build -t storage-prepare:7 prepare
k3d image import storage-prepare:7 -c "$CLUSTER"
kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic s3-root -n storage --from-literal=key="$S3_ROOT_KEY" \
  --from-literal=secret="$S3_ROOT_SECRET" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic accounts -n storage --from-literal=upload-secret="$S3_UPLOAD_SECRET" \
  --from-literal=links-secret="$S3_LINKS_SECRET" --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap storage-config -n storage \
  --from-file=upload.json=policies/upload.json --from-file=links.json=policies/links.json \
  --from-file=personal-space.json=policies/personal-space.json --from-file=lifecycle.json=lifecycle.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s.yaml
kubectl rollout status deployment/s3 -n storage --timeout=180s
job --prepare
