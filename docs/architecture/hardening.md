# Hardening

How the images, the containers, the web server and the release process are locked down, what is
left to the operator, and how to scan the images yourself. For operators and security reviewers.

## Images

- **No root.** Both images run as user 10001; every service in `compose.yaml` runs as
  `UC_UID:UC_GID` (default 10001). `tini` is PID 1 in the uc image.
- **Pinned bases.** The uc image builds on `eclipse-temurin:21-jre` and the web image on
  `httpd:2.4`, each pinned by digest. Dependabot proposes digest updates weekly.
- **Small installs.** Packages are installed without recommended extras, and the apt lists are
  removed.
- **No imports from the working directory.** The uc image sets `PYTHONSAFEPATH=1`, so
  `python3 -m uc` never imports a file someone placed in its working directory, next to the signing
  key.

## Containers

Every service (`uc`, `scheduler`, `repo-adapter`, `web` and `setup`) runs with:

- all Linux capabilities dropped and `no-new-privileges`;
- a read-only root filesystem, with a tmpfs only where it needs scratch space;
- a process limit of 512 and rotated logs.

Each service mounts only the secrets it uses: `web` never sees the signing key, and only the
`scheduler` and `uc` hold it. The `repo` network, between the repo-adapter and the services that use
it, is internal: nothing on it reaches outside the host, `web` is not on it, and the repo-adapter
reaches the repository through a network of its own. `make compose-check` enforces these
boundaries in CI ([Network](../reference/network.md), [Secrets](../reference/secrets.md)).

## Web server

- `ServerTokens Prod`, `ServerSignature Off` and `TraceEnable off`.
- `X-Content-Type-Options: nosniff` on every answer, and `Strict-Transport-Security` with TLS on.
- `mod_reqtimeout`, against clients that hold connections open by sending slowly.
- `/download/` answers only `GET` and `HEAD` (405 otherwise), and drops the client's query string,
  so in proxy mode nothing but a plain download reaches the repository with the proxy's credentials.
- The proxy and TLS modules are loaded only when those modes are on.

## Untrusted input

Plugin archives, manifests, POMs and repository listings are treated as untrusted:

- every read has a size limit, whatever an archive claims ([repo-adapter limits](repo-adapter.md));
- nothing is extracted to a path taken from an archive;
- values from repositories and manifests must match their pattern in full, so a trailing newline
  is refused;
- a malformed answer is reported as an error, not a crash.

## Signing keys

The root CA's key is needed only to issue the next signing certificate; setup warns while it is on
the host, and the guides move it offline. The signing certificate lasts 180 days, and generation
stops 30 days before it expires (`CERTIFICATE_MINIMUM_VALID_DAYS`)
([Signing and trust](signing.md)).

## Releases

- Release images are built without cache, CI tests those exact digests, and only then are they
  signed and tagged.
- Images are signed with cosign, keyless, by the Release workflow alone. Their build provenance and
  SBOM are attested, and `SHA256SUMS` for the bundles is signed
  ([Verify a release](../how-to/verify-a-release.md)).
- GitHub Releases are immutable, only admins can push release tags, and every action in the
  workflows is pinned by commit.
- Pre-commit runs actionlint, zizmor and a secret check on every commit, CodeQL scans every pull
  request, and GitHub's secret scanning with push protection guards the repository.

## Vulnerability scanning

Every Monday, the `image-scan` job of `.github/workflows/scheduled.yml` scans the latest release's
images with Grype. It opens one issue listing the High and Critical vulnerabilities that have a fix,
updates it on later runs, and closes it once none remain. Fixes ship as a new release on updated
base images
([Security fixes in the base images](../development/releasing.md#security-fixes-in-the-base-images)).

To scan a release yourself, with the version in place of `<version>`:

```bash
grype ghcr.io/haletechco/easy-update-center-uc:<version>
grype ghcr.io/haletechco/easy-update-center-web:<version>
trivy image ghcr.io/haletechco/easy-update-center-uc:<version>
trivy image ghcr.io/haletechco/easy-update-center-web:<version>
```

Both tools also run as containers (`anchore/grype`, `aquasec/trivy`). Findings come from three
places:

- the base images' operating-system packages (Ubuntu in the uc image, Debian in the web image),
  most with no fix released yet;
- update-center2's Java libraries, at the versions upstream's build pins;
- Go programs in the uc image (supercronic, and `pebble` from its base image), fixed by updating
  them.

The weekly issue lists only the High and Critical ones with a fix.

## Left to the operator

- **TLS.** Plain HTTP is for trials. Production needs `https://`
  ([Enable TLS](../how-to/enable-tls.md)).
- **Memory and CPU limits.** None are set: generating the full catalog needs several GB, so a fixed
  cap could break it. Add limits that suit your catalog in an override file.
- **The host.** The Docker daemon, the host's updates and its firewall are outside this project.
- **The root key.** Keep it offline, with a backup.
