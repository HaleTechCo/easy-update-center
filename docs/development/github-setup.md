# Set up the GitHub repository

The owner's one-time steps for the GitHub repository of Easy Update Center for Jenkins, in order.
Everything here is free for a public repository in an organization on GitHub Free: Actions
(including the `ubuntu-24.04-arm` runners, free for public repositories only), public packages on
GHCR, build provenance and SBOM attestations, rulesets, Dependabot, and private vulnerability
reporting. Nothing in the workflows needs a paid plan, larger runners or an organization-wide feature.

## 1. Organization settings (HaleTechCo)

- Require two-factor authentication.
- Members' base permission: Read.
- Actions > General: workflow permissions "Read repository contents and packages permissions", and
  "Allow GitHub Actions to create and approve pull requests" off.
- Packages: package creation allows public packages.

## 2. Create the repository

`easy-update-center` in HaleTechCo, **public**, empty: no README, license or .gitignore. It must be
public before the first release: the `ubuntu-24.04-arm` runners the release builds on are free only
for public repositories.

## 3. Push

In your checkout:

```bash
git branch -m master main
git remote rename origin gitlab
git remote add origin https://github.com/HaleTechCo/easy-update-center.git
git push -u origin main
git push origin <tag>        # for each tag you keep
```

Do this before the tag ruleset (step 6) exists. The push runs CI once, which makes the `ci` check
selectable in step 5.

## 4. Repository settings

- The same Actions settings as in step 1, if the organization's do not already apply.
- Features: Issues on, Wikis off.
- Leave "Immutable releases" off. A re-run with publish-only replaces the Release's assets
  (`gh release upload --clobber`), which immutable releases forbid.

## 5. Branch ruleset

Settings > Rules > Rulesets > New branch ruleset.

- Name `main`, enforcement Active, target the default branch.
- Bypass list: **GitHub Actions** (the app), bypass mode **Always**, and nothing else. Always covers
  both the required `ci` check and the pull-request rule below, which the release commit skips.
- Rules: restrict deletions; require a pull request before merging, with 0 required approvals
  (GitHub does not let the one maintainer approve their own pull request); require status checks to
  pass, with `ci` (source GitHub Actions); block force pushes.

Why the GitHub Actions app: the Release workflow's `prepare` job pushes the release commit to
`main`. GitHub accepts that app as a bypass actor only in an organization's repository. The bypass
names the whole app, so what limits it to the release is that only `release.yml`'s `prepare` and
`release` jobs hold `contents: write` (pinned by `tests/unit/test_workflows.py`).

## 6. Tag ruleset

Name `release tags`, target `v*`, rules: restrict creations, restrict updates, restrict deletions.
The same bypass list as step 5. Only the Release workflow creates release tags.

## 7. Security and moderation

- Settings > Advanced Security: enable private vulnerability reporting
  ([SECURITY.md](../../SECURITY.md) points reporters to it) and Dependabot alerts. Secret scanning and
  push protection are already on for public repositories.
- Settings > Moderation options > Reported content: accept reports from **All users**, so anyone can
  report a comment, issue or pull request to the admins privately, as the Enforcement section of
  [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md) says (free for public repositories owned by an
  organization). Reports appear under the repository's reported content list for admins to review.

Dependabot's version updates need no setting: `.github/dependabot.yml` opens one weekly pull request
each for the actions, the base images and the lint tools.

## 8. Secrets

Settings > Secrets and variables > Actions: `JENKINS_COMMUNITY_USERNAME` and
`JENKINS_COMMUNITY_PASSWORD`, a free Jenkins community account, for the weekly `it-online` run.
Without them the run is skipped with a notice.

## 9. After the first release

New GHCR packages start private, and anonymous pulls fail until they are public. For
`easy-update-center-uc` and `easy-update-center-web` (the organization's Packages): Package settings >
Change visibility > Public. Check that each is linked to the repository (the images'
`org.opencontainers.image.source` label does that; otherwise Connect repository).

## 10. Archive GitLab

Set the GitLab project's description to "Moved to https://github.com/HaleTechCo/easy-update-center",
replace its README on GitLab with one line pointing there (GitLab's web editor), then Settings >
General > Advanced > Archive project.
