# Upgrading update-center2

The jar is pinned in `uc/Dockerfile` (`UC2_VERSION`, `UC2_COMMIT`). Security releases change
generated HTML and JSON, so upgrade promptly. Resource data (warnings, suspensions) is refreshed on
every online run regardless.

1. See what changed in the files this project depends on:

   ```bash
   make upstream-diff TO=3.19
   ```

   Review each diff against the spec:

   | Upstream change | What to check |
   |---|---|
   | `site/generate.sh` | argument lines in `uc/uc/args.py` (§6 step 6) and the layout in `uc/uc/layout.py` (§6 step 8) |
   | `site/generate-htaccess.sh` | `uc/uc/htaccess.py`: the five download rules and the partial-tier strings must still match exactly (the run fails loudly if not) |
   | `site/test/test.sh` | port new expectations into `tests/routing/test_upstream_rules.py` |
   | `Main.java`, `DirectoryTreeBuilder.java`, `MetadataWriter.java`, `Signer.java` | removed or renamed options used in `args.py` (`--connection-check-url` is the one upstream's own script does not use) |
   | `MavenArtifact.java` | `DOWNLOADS_ROOT_URL` still controls download URLs |
   | `ArtifactoryRepositoryImpl.java` | AQL query (`uc/uc/aql.py`), cache layout (`uc/uc/doctor.py`), the Authenticator port check (§10), and the three requests the repo-adapter answers (repo-adapter spec §2) |
   | `HPI.java` | the archive entries the repo-adapter serves: `META-INF/MANIFEST.MF` and `<artifactId>-<version>.jar!/index.jelly` (repo-adapter spec §4.2) |
   | `Popularities.java` | whether `0001-popularity-configurable-and-optional.patch` still applies, or is no longer needed |
   | `json/TieredUpdateSitesGenerator.java` | whether `0002-core-age-configurable.patch` still applies, or is no longer needed |

2. Update `uc/Dockerfile`: set `UC2_VERSION`, and set `UC2_COMMIT` to `git rev-parse update-center2-<version>^{commit}`.
   If upstream has merged both changes, delete the patches and drop the `git apply` step; the
   `jar-build` stage can then take the generator jar from the released
   `update-center2-<version>-bin.zip` on `repo.jenkins-ci.org`, pinned by SHA-256, instead of running
   `mvn verify`. Keep the pinned `git clone` in that stage regardless: it is still where
   `site/generate-htaccess.sh` and `resources/` come from (the `-bin.zip` contains only jars), and
   `UC2_COMMIT` still pins the source those two are copied from.
3. Update the verified-facts list in the implementation plan, and the version in the README and spec header.
4. Run `make test`, `make it-airgapped`, then `make it-online` (needs community credentials).
5. Deploy: `docker compose build` is not used (images are built by `make build`), so
   `make build && docker compose up -d`. The next scheduled run uses the new generator; run
   `docker compose run --rm uc generate` to publish immediately.
