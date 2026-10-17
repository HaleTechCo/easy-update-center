"""A literal copy of the AQL query update-center2 sends, for the tests.

So that a change to uc's own copy of it is a change from upstream's.
"""

# update-center2's ArtifactoryRepositoryImpl.AQL_QUERY for the repository 'jenkins'
UPSTREAM_QUERY = (
    'items.find({"repo":{"$eq":"jenkins"},"$or":[{"name":{"$match":"*.hpi"}},'
    '{"name":{"$match":"*.jpi"}},{"name":{"$match":"*.war"}},'
    '{"name":{"$match":"*.pom"}}]}).include("repo", "path", "name", "modified", '
    '"created", "sha256", "actual_sha1", "size")'
)


def upstream_query(repository: str) -> str:
    """Return upstream's query for another repository key."""
    return UPSTREAM_QUERY.replace('"$eq":"jenkins"', f'"$eq":"{repository}"')
