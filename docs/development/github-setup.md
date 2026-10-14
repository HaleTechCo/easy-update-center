# GitHub repository settings

The settings of Easy Update Center for Jenkins's GitHub repository that the workflows rely on, in the
order to apply them. Everything here is free for a public repository in an organization on GitHub Free: Actions
(including the `ubuntu-24.04-arm` runners, free for public repositories only), public packages on
GHCR, build provenance and SBOM attestations, rulesets, Dependabot, and private vulnerability
reporting. Nothing in the workflows needs a paid plan, larger runners or an organization-wide feature.

## 1. Organization settings (HaleTechCo)

- Require two-factor authentication.
- Members' base permission: Read.
- Actions > General: workflow permissions "Read repository contents and packages permissions", and
  "Allow GitHub Actions to create and approve pull requests" off.
- Packages: package creation allows public packages.

## 2. The repository

`easy-update-center` in HaleTechCo, **public**. It must stay public: the `ubuntu-24.04-arm` runners
the release builds on are free only for public repositories.

## 3. Repository settings

- The same Actions settings as in step 1, if the organization's do not already apply.
- Features: Issues on, Wikis off.
- Settings > General > Releases: **Enable release immutability**. A published Release's assets and tag
  can never change; it applies to Releases published from then on. The Release workflow uploads to a
  draft and publishes it last, which immutable releases allow.

## 4. Branch ruleset

Settings > Rules > Rulesets > New branch ruleset.

- Name `main`, enforcement Active, target the default branch, and an empty bypass list: every change
  to `main`, the release commit included, arrives by pull request.
- Rules: restrict deletions; require a pull request before merging, with 0 required approvals
  (GitHub does not let the one maintainer approve their own pull request); require status checks to
  pass, with `ci` (source GitHub Actions); block force pushes.

## 5. Tag ruleset

Name `release tags`, target `v*` (which covers release-candidate tags such as `v0.1.0-rc.1` too),
rules: restrict creations, restrict updates, restrict deletions.
Bypass list: the **Repository admin** role, bypass mode Always. Only admins can then push, move or
delete a release tag, and pushing one is what publishes a release
([Releasing](releasing.md#cut-a-release)).

Why this holds: no workflow token can push to `main` or create a tag. Only `release.yml`'s `draft`
and `publish` jobs hold `contents: write`, to create and publish the GitHub Release on the tag that
already exists, and the
Release workflow's `check` job refuses a tag on a commit that is not on `main`. Keep the admin role
to people you would let publish a release (today one maintainer, with members' base permission
Read).

## 6. Security and moderation

- Settings > Advanced Security: enable private vulnerability reporting
  ([SECURITY.md](../../SECURITY.md) points reporters to it) and Dependabot alerts. Secret scanning and
  push protection are already on for public repositories.
- Settings > Advanced Security > Code scanning: **CodeQL analysis**, Default setup, with the
  languages Python and GitHub Actions. Free for public repositories.
- Settings > Moderation options > Reported content: accept reports from **All users**, so anyone can
  report a comment, issue or pull request to the admins privately, as the Enforcement section of
  [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md) says (free for public repositories owned by an
  organization). Reports appear under the repository's reported content list for admins to review.

Dependabot's version updates need no setting: `.github/dependabot.yml` opens one weekly pull request
each for the actions, the base images and the lint tools.

## 7. Secrets

Settings > Secrets and variables > Actions: `JENKINS_COMMUNITY_USERNAME` and
`JENKINS_COMMUNITY_PASSWORD`, a free Jenkins community account, for the weekly `it-online` run.
Without them the run is skipped with a notice.

## 8. After the first release

New GHCR packages start private, and anonymous pulls fail until they are public. For
`easy-update-center-uc` and `easy-update-center-web` (the organization's Packages): Package settings >
Change visibility > Public. Check that each is linked to the repository (the images'
`org.opencontainers.image.source` label does that; otherwise Connect repository).
