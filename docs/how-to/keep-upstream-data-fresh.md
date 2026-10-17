# Keep upstream data fresh

How an air-gapped update center gets current security warnings, suspensions, popularity and tool installers.

Online mode fetches all of this at every run; this page is for air-gapped mode.

## What goes stale

| Data | Where it comes from | Air-gapped default |
|---|---|---|
| Security warnings, plugin suspensions, label definitions | update-center2's `resources/` directory | The copy built into the image, as old as the image's update-center2 version |
| Popularity (install counts) | Jenkins infra statistics | Skipped: every plugin shows 0 |
| Tool installers (`updates/`: JDK, Maven, Gradle, NodeJS and others) | updates.jenkins.io | Skipped: no tool installers |

Security warnings matter most: Jenkins shows them to administrators for the plugins they run. The
Jenkins project updates them with every advisory. The site still works without fresh copies; it
just shows what it knew when the image was built.

The bundled copy is dated by the update-center2 commit the image pins, not by when you built the
image: `status.json` shows it as `resources.data_date` (for update-center2 3.18.4, 2026-09-02). It
counts as stale (`resources.stale`) once it is older than `STALE_AFTER_DAYS` (30), so a new
air-gapped installation on bundled data can report stale data within weeks. Set up a procured copy
when you deploy.

## Security warnings and suspensions

Procure a tarball of upstream's update-center2 repository, which contains `resources/`, for example
from GitHub's archive of the `master` branch:

```text
https://github.com/jenkins-infra/update-center2/archive/refs/heads/master.tar.gz
```

Store it where the update center can reach it, note its SHA-256, and set the three variables below.
The `scheduler` and `uc` containers fetch `RESOURCES_URL`, `POPULARITY_JSON_URL` and
`TOOL_INSTALLERS_URL` themselves, **without credentials**, and verify HTTPS only against a public
trust store, never `REPO_CA_FILE`. The resources tarball and the tool installers are fetched by
`uc`, so they use the image's system CA bundle and `HTTPS_PROXY`; the popularity file is fetched by
the generator's JVM, so it uses the Java truststore and the proxy flags in `JAVA_TOOL_OPTIONS`
([configuration](../reference/configuration.md#read-by-bundled-software)). So serve these files from a
location that allows anonymous reads: for example an Artifactory generic repository with anonymous
read access, over HTTPS with a publicly trusted certificate, or over plain HTTP inside your network.
`RESOURCES_SHA256` still verifies the tarball whatever the transport.

```bash
RESOURCES_SOURCE=url
RESOURCES_URL=https://artifactory.corp.example/artifactory/generic-local/update-center2-master.tar.gz
RESOURCES_SHA256=<sha256 of the tarball>
```

Any tarball containing `resources/warnings.json` works. Replace the file (and the checksum) on each
procurement; the next generation picks it up. If a fetch fails, the run keeps the last good copy and
warns. Your own warnings and blocks in the [overlay](block-plugins-and-add-warnings.md) apply on top
either way.

## Popularity

Procure a copy of:

```text
https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json
```

and set `POPULARITY_JSON_URL` to where you store it. Jenkins uses popularity to sort search results.

## Tool installers

Procure the `*.json` and `*.json.html` files from `https://updates.jenkins.io/updates/` and serve
them from a directory whose listing names them (an Artifactory generic repository's folder listing
works). Set `TOOL_INSTALLERS_URL` to that directory, with a trailing slash:

```bash
TOOL_INSTALLERS_URL=https://artifactory.corp.example/artifactory/generic-local/updates/
```

The files keep the Jenkins project's signatures and are published unmodified. Each installer's own
download links still point at the internet, so an air-gapped controller can use them only if you
mirror those downloads too.

## Check the age

`status.json`'s `resources` block shows which copy the live release used and how old it is
([status.json](../reference/status-json.md)). Alert on `resources.stale`: see [Monitor](monitor.md).
