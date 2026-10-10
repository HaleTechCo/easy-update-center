# Proxy plugin downloads

How to stream plugin and core downloads through the web container when controllers cannot read the repository themselves.

## When to use it

By default (`DOWNLOAD_ACCESS=redirect`) the update center answers each download with a redirect,
and the controller fetches the file from the repository itself, without credentials. Use proxy mode
when:

- the repository requires authentication for reads, or
- controllers have no network route to the repository, only to the update center.

In proxy mode the web container fetches each file from `DOWNLOAD_BACKEND_URL` with a read-only
account and streams it to the controller. Nothing is cached, so every download still reaches the
repository.

## Enable it

1. Create a read-only account on the repository and write it as `username:password` (for Artifact
   Keeper, a user name and API token):

   ```bash
   printf '%s' 'svc-read:password-or-token' > secrets/download_proxy_auth
   docker compose run --rm setup secrets
   ```

   `setup secrets` makes the file readable by you, the user the containers run as, alone
   ([File modes](../reference/secrets.md#file-modes)).

2. Set `DOWNLOAD_ACCESS=proxy` in `.env`.
3. Recreate the web container: `docker compose up -d`.

The web container refuses to start in proxy mode if the file does not hold `username:password`. To
check, download a plugin you know the repository holds through it, using your own `PUBLIC_URL`:

```bash
PUBLIC_URL=https://jenkins-updates.corp.example
curl -sSo /dev/null -w '%{http_code}\n' -L "$PUBLIC_URL/download/plugins/git/latest/git.hpi"
```

Expect `200`. A `502` means the backend's certificate did not verify (next section); a `401` or
`403` means the account cannot read the file.

## Backend with a private CA

The web container verifies the backend's TLS certificate against `DOWNLOAD_PROXY_CA_FILE`, the system
CA bundle by default. A backend with a certificate from a private or corporate CA returns 502 on
every download until you mount that CA's bundle:

```yaml
# compose.proxy-ca.yaml
services:
  web:
    volumes:
      - ./secrets/proxy-ca.crt:/etc/ssl/uc-proxy-ca.crt:ro
    environment:
      DOWNLOAD_PROXY_CA_FILE: /etc/ssl/uc-proxy-ca.crt
```

Copy the CA's PEM file to `secrets/proxy-ca.crt`, run `docker compose run --rm setup secrets`, add
the override to `COMPOSE_FILE` in `.env` ([Compose](../reference/configuration.md#compose)), and
recreate the web container:

```bash
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.proxy-ca.yaml    # in .env
```

```bash
docker compose up -d
```

The file must hold every CA needed to verify the backend (intermediate CAs are accepted). It
replaces the system bundle for this purpose, it is not added to it.
