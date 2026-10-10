# Upgrading update-center2

Maintainer guide: moving the pinned update-center2 generator to a newer release. The Scheduled
workflow (`.github/workflows/scheduled.yml`) checks weekly for an update-center2 release newer than
the pin, and opens an issue titled "update-center2 <version> is available" with `make
upstream-diff`'s summary and whether the patches still apply.

The generator is pinned in `uc/Dockerfile` (`UC2_VERSION`, `UC2_COMMIT`). Upstream's security
releases change the generated HTML and JSON, so upgrade promptly. Resource data (security warnings,
suspensions) does not wait for an upgrade: online runs fetch it at every generation.

## 1. Review upstream changes

See what changed in the files this project depends on:

```bash
make upstream-diff TO=3.19
```

It prints the full diff stat between the pinned version and `TO`, the diff of every upstream file
listed below, and whether the patches still apply. Review each diff:

| Upstream change | What to check |
|---|---|
| `site/generate.sh` | The argument lines in `uc/uc/args.py` ([Step 6](../architecture/generation-pipeline.md#step-6-arguments-file)) and the layout in `uc/uc/layout.py` ([Step 8](../architecture/generation-pipeline.md#step-8-layout-and-download-map)). |
| `site/generate-htaccess.sh` | `uc/uc/htaccess.py`: the five download rules and the partial-tier strings must still match exactly; the run fails loudly if not ([Step 5](../architecture/generation-pipeline.md#step-5-htaccess)). |
| `site/test/test.sh` | Port new expectations into `tests/routing/test_upstream_rules.py`. |
| `Main.java`, `DirectoryTreeBuilder.java`, `MetadataWriter.java`, `Signer.java` | Removed or renamed options used in `uc/uc/args.py`. `--connection-check-url` is the one upstream's own script does not use. |
| `MavenArtifact.java` | `DOWNLOADS_ROOT_URL` still controls download URLs. |
| `ArtifactoryRepositoryImpl.java` | The listing query (`uc/uc/aql.py`), the cache layout (`uc/uc/doctor.py`), the authenticator's port check ([Step 3](../architecture/generation-pipeline.md#step-3-environment-for-the-generator)), and the three requests the repo-adapter answers ([Requests it answers](../architecture/repo-adapter.md#requests-it-answers)). |
| `HPI.java` | The archive entries the repo-adapter serves: `META-INF/MANIFEST.MF` and `<artifactId>-<version>.jar!/index.jelly` ([Filling gaps](../architecture/repo-adapter.md#filling-gaps)). |
| `Popularities.java` | Whether `0001-popularity-configurable-and-optional.patch` still applies, or is no longer needed. |
| `json/TieredUpdateSitesGenerator.java` | Whether `0002-core-age-configurable.patch` still applies, or is no longer needed. |

## 2. Update the pin

In `uc/Dockerfile`, set `UC2_VERSION` to the new version, and `UC2_COMMIT` to the tag's commit:

```bash
git -C /path/to/update-center2 rev-parse 'update-center2-3.19^{commit}'
```

The build checks out the tag and fails if its commit differs, and fails if a patch does not apply.

If upstream has merged both patches, delete them and the `git apply` step. The `jar-build` stage can
then take the generator jar from the released `update-center2-<version>-bin.zip` on
repo.jenkins-ci.org, pinned by SHA-256, instead of running `mvn verify`. Keep the pinned `git clone`
in that stage either way: it is still where `site/generate-htaccess.sh` and `resources/` come from
(the `-bin.zip` contains only jars), and `UC2_COMMIT` still pins the source they are copied from.

## 3. Update the docs

- The version in [Dependencies](../reference/dependencies.md) (and in `README.md` if it names it).
- A `CHANGELOG.md` entry under "Unreleased".
- Any page whose behavior the upgrade changed.
- The third-party notices, if the build asks. `make build` fails when a new library has no known
  license ("There are some dependencies with no license") or a shipped jar is missing from the list
  ("no third-party notice for ..."). Find the library's license in its source or repository and
  record it, with the evidence, in `uc/licenses/THIRD-PARTY.properties`; for a license the image does
  not carry yet, add its text to `uc/licenses/` and a line to `uc/licenses/NOTICE.txt`. See
  [Third-party notices](../reference/dependencies.md#third-party-notices).

## 4. Test

```bash
make build
make test
make it-airgapped
make it-online        # needs JENKINS_COMMUNITY_USERNAME and JENKINS_COMMUNITY_PASSWORD
```

## 5. Deploy

Installations pick it up like any other revision: [Upgrade](../how-to/upgrade.md). The next
scheduled run uses the new generator; `docker compose run --rm uc generate` publishes at once.

## The patches

Two small patches in `uc/patches/` are applied to update-center2 at build time:

- `0001-popularity-configurable-and-optional.patch` (`Popularities.java`): the popularity URL
  becomes `POPULARITY_JSON_URL`, following the pattern that file already uses for five other URLs,
  and an empty URL or a failed fetch logs a warning and uses popularity 0 instead of failing the
  run. Upstream hard-codes the URL and treats a failure as fatal, which makes air-gapped runs
  impossible.
- `0002-core-age-configurable.patch` (`json/TieredUpdateSitesGenerator.java`): the 400-day core
  age for tiers becomes `CORE_AGE_DAYS`, where `0` means no limit. See
  [Core age](../architecture/modes.md#core-age).

Both are meant to go upstream. Once a release contains them, drop them as described in
[step 2](#2-update-the-pin).
