# Enable TLS

How to serve the update center over HTTPS, behind your own proxy or from the web container itself.

HTTPS is required for production. Signatures stop anyone from changing the metadata, but over plain
HTTP anyone on the network path between a controller and the update center can still:

- **replay older metadata.** Jenkins accepts any file signed by a certificate that is still valid,
  however old, so an attacker can keep serving last month's file and hide every security warning
  published since.
- **swap in another signed file.** Jenkins trusts the Jenkins project's own roots alongside yours,
  for every update site, so updates.jenkins.io's genuine file passes as yours, without your blocked
  plugins and your own warnings.
- **send a controller to an older tier.** The `?version=` redirects are not signed.
- **take over `jenkins-plugin-cli`.** It checks no signatures at all, so over HTTP it installs
  whatever plugins and checksums it is sent.

Plain HTTP is fine for a trial on one machine. `uc config --check` and every run warn while
`PUBLIC_URL` uses it.

## Behind a reverse proxy

The web container serves plain HTTP on port 8080 (`HTTP_PORT` on the host). To terminate TLS in a
proxy or ingress you already run:

1. Set `PUBLIC_URL` to the proxy's `https://` origin, for example
   `https://jenkins-updates.example.com`.
2. Forward every path and query string unchanged to the web container's port 8080. The query
   string matters: version routing depends on `?version=`.
3. If a release was already published under a different `PUBLIC_URL`, publish one with the new URL:
   `docker compose run --rm uc generate`. On a fresh installation the first run already uses it.

Redirects always name `PUBLIC_URL`, whatever `Host` header the proxy sends, so no rewriting is
needed in the proxy.

Keep port 8080 away from everyone but the proxy, especially in proxy download mode, where it would
serve repository files over plain HTTP. `HTTP_PORT` accepts a bind address: with the proxy on the
same host, `HTTP_PORT=127.0.0.1:8080` in `.env` publishes it on the loopback interface only.
Otherwise restrict it in the
`DOCKER-USER` chain: ports Docker publishes bypass ufw and firewalld rules.

## In the web container

With TLS on, port 8080 answers every request with a redirect to
`PUBLIC_URL`, which must then start with `https://`. Do not rely on the redirect: change each
controller's update site to the https URL.

Mount a certificate and key and point `TLS_CERT_FILE` and `TLS_KEY_FILE` at them, in a Compose
override file next to `compose.yaml`:

```yaml
# compose.tls.yaml
services:
  web:
    environment:
      TLS_CERT_FILE: /tls/tls.crt
      TLS_KEY_FILE: /tls/tls.key
    volumes:
      - ./tls:/tls:ro
```

Put the PEM certificate (with its chain) and key in `./tls/`, readable by `UC_UID` on Linux. Then add
the override to `COMPOSE_FILE` in `.env` ([Compose](../reference/configuration.md#compose)):

```bash
COMPOSE_FILE=compose.yaml:compose.tls.yaml                              # online
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.tls.yaml       # air-gapped
```

Set `PUBLIC_URL=https://...` in `.env` before `docker compose up -d`: with the certificate and key
set, the web container refuses to start while `PUBLIC_URL` is not `https://`. Then run
`docker compose up -d`. Port 8443 (`HTTPS_PORT` on the host) then serves TLS with Mozilla's
"intermediate" settings and 8080 only redirects to it.

`PUBLIC_URL` must name the port controllers actually reach. `HTTPS_PORT` defaults to 8443, so either
set `HTTPS_PORT=443` in `.env` and use `https://jenkins-updates.example.com`, or keep 8443 and put
it in the URL, `https://jenkins-updates.example.com:8443`. Publish a new release if a release
was already published under a different `PUBLIC_URL`: `docker compose run --rm uc generate`.
