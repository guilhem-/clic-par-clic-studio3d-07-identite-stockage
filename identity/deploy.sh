#!/usr/bin/env bash
# The organization's identity provider (module 7): Keycloak 26.8.0 in the "identity" namespace.
#   ./identity/deploy.sh                       deploys (or updates) Keycloak and imports the realm
#   ./identity/deploy.sh --plug-open-webui     Open WebUI signs in through Keycloak (variables, restart)
#   ./identity/deploy.sh --unplug-open-webui   removes those variables (original sign-in page)
#   ./identity/deploy.sh --token <user> [client]   a user's token through a test client (direct grant)
# Secrets in identity/.env (created on first run, never committed). OWUI_NAMESPACE and OWUI_DEPLOYMENT:
# where your Open WebUI runs (default: open-webui / open-webui).
set -euo pipefail
cd "$(dirname "$0")"
ENV=.env
[ -f "$ENV" ] || {
  echo "KEYCLOAK_ADMIN_PASSWORD=$(openssl rand -hex 18)" > "$ENV"
  for u in CAMILLE LUKAS OLIVER; do echo "PASSWORD_${u}=$(openssl rand -hex 18)" >> "$ENV"; done
  for c in OPEN_WEBUI STUDIO3D STORAGE; do echo "SECRET_${c}=$(openssl rand -hex 18)" >> "$ENV"; done
}
set -a; . "./$ENV"; set +a
ISSUER=http://sso.localhost:8080/realms/example-org
OWUI_NAMESPACE=${OWUI_NAMESPACE:-open-webui}
OWUI_DEPLOYMENT=${OWUI_DEPLOYMENT:-open-webui}

case "${1:-}" in
  --plug-open-webui)
    kubectl create secret generic open-webui-oidc -n "$OWUI_NAMESPACE" --from-literal=OAUTH_CLIENT_SECRET="$SECRET_OPEN_WEBUI" \
      --dry-run=client -o yaml | kubectl apply -f -
    mapfile -t vars < <(grep -E '^[A-Z_]+=' open-webui.env)   # one variable per line, spaces kept
    kubectl set env "deploy/$OWUI_DEPLOYMENT" -n "$OWUI_NAMESPACE" "${vars[@]}"
    kubectl set env "deploy/$OWUI_DEPLOYMENT" -n "$OWUI_NAMESPACE" --from=secret/open-webui-oidc
    kubectl rollout status "deploy/$OWUI_DEPLOYMENT" -n "$OWUI_NAMESPACE" --timeout=300s
    exit ;;
  --unplug-open-webui)
    # shellcheck disable=SC2046
    kubectl set env "deploy/$OWUI_DEPLOYMENT" -n "$OWUI_NAMESPACE" $(grep -oE '^[A-Z_]+' open-webui.env | sed 's/$/-/') OAUTH_CLIENT_SECRET-
    kubectl delete secret open-webui-oidc -n "$OWUI_NAMESPACE" --ignore-not-found
    exit ;;
  --token)
    user=$2; client=${3:-test}; var="PASSWORD_$(echo "$user" | tr '[:lower:]' '[:upper:]')"
    curl -fsS "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id="$client" \
      -d username="$user" --data-urlencode password="${!var}" -d scope=openid | sed -E 's/.*"access_token":"([^"]+)".*/\1/'
    exit ;;
esac

realm=$(sed -e "s/__PASSWORD_CAMILLE__/$PASSWORD_CAMILLE/" -e "s/__PASSWORD_LUKAS__/$PASSWORD_LUKAS/" \
  -e "s/__PASSWORD_OLIVER__/$PASSWORD_OLIVER/" -e "s/__SECRET_OPEN_WEBUI__/$SECRET_OPEN_WEBUI/" \
  -e "s/__SECRET_STUDIO3D__/$SECRET_STUDIO3D/" -e "s/__SECRET_STORAGE__/$SECRET_STORAGE/" realm.json)
kubectl create namespace identity --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic keycloak-admin -n identity --from-literal=username=admin \
  --from-literal=password="$KEYCLOAK_ADMIN_PASSWORD" --dry-run=client -o yaml | kubectl apply -f -
printf '%s' "$realm" | kubectl create secret generic realm -n identity --from-file=realm.json=/dev/stdin \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s.yaml
kubectl rollout restart deployment/keycloak -n identity   # H2 database in the container: the realm is imported again
kubectl rollout status deployment/keycloak -n identity --timeout=300s
until curl -fsS "$ISSUER/.well-known/openid-configuration" >/dev/null; do sleep 2; done
echo "Keycloak ready: $ISSUER"
