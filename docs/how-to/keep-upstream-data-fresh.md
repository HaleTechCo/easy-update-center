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

## Security warnings and suspensions

Procure a tarball of upstream's update-center2 repository, which contains `resources/`, for example
from GitHub's archive of the `master` branch:

```text
https://github.com/jenkins-infra/update-center2/archive/refs/heads/master.tar.gz
```

Store it where the update center can reach it (an Artifactory generic repository works), note its
SHA-256, and set:

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

`status.json` reports the upstream data behind the live release:

- `resources.source` and `resources.ref`: which copy was used;
- `resources.age_days`: days since upstream last changed it;
- `resources.stale`: `true` once that exceeds `STALE_AFTER_DAYS` (30).

Alert on `resources.stale`: see [Monitor](monitor.md).
