"""Unit tests for uc.pom.

POM coordinates and the reasons a repository POM is unusable.
"""

import pytest

from archivefixture import pom_xml
from uc.pom import (
    COORDINATE,
    GROUP_ID,
    dependency_group,
    pom_coordinates,
    safe_root,
    unusable_reason,
)

GIT = ('org.jenkins-ci.plugins', 'git', '5.2.0')
NGINX_404 = (
    b'<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n'
    b'<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n'
    b'</body>\r\n</html>\r\n'
)


def test_coordinates_of_a_real_pom():
    assert pom_coordinates(pom_xml(*GIT)) == GIT


def test_group_and_version_may_come_from_the_parent():
    data = (
        b'<project><parent><groupId>org.jenkins-ci.plugins</groupId>'
        b'<artifactId>plugin</artifactId><version>5.2.0</version></parent>'
        b'<artifactId>git</artifactId></project>'
    )
    assert pom_coordinates(data) == GIT


def test_a_usable_pom_has_no_reason():
    assert unusable_reason(pom_xml(*GIT), GIT) is None


@pytest.mark.parametrize(
    ('data', 'reason'),
    [
        (
            NGINX_404,
            'not XML',
        ),  # the saved error page from the operator's test repository
        (b'<html><body>404</body></html>', 'not a POM (root element <html>)'),
        (
            pom_xml('io.jenkins.plugins', 'git', '5.2.0'),
            (
                'declares io.jenkins.plugins:git:5.2.0, '
                'not org.jenkins-ci.plugins:git:5.2.0'
            ),
        ),
        (
            b'<project><artifactId>git</artifactId></project>',
            'does not declare groupId, artifactId and version',
        ),
        (b'<!DOCTYPE p [<!ENTITY x "y">]><project/>', 'declares a DOCTYPE or entities'),
        (
            (
                b'<project><groupId>org.jenkins-ci.plugins</groupId>'
                b'<artifactId>git</artifactId>'
                b'<version>${revision}${changelist}</version></project>'
            ),
            (
                'declares org.jenkins-ci.plugins:git:${revision}${changelist}, '
                'not org.jenkins-ci.plugins:git:5.2.0'
            ),
        ),
    ],
)
def test_unusable_poms_say_why(data, reason):
    assert unusable_reason(data, GIT) == reason


def test_a_latin1_pom_is_usable():
    data = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<project><groupId>org.jenkins-ci.plugins</groupId>'
        '<artifactId>git</artifactId><version>5.2.0</version>'
        '<name>Plugin by J\u00e9r\u00f4me</name></project>'
    ).encode('latin-1')
    assert unusable_reason(data, GIT) is None


def test_safe_root_refuses_what_the_byte_check_cannot_see():
    assert safe_root(b'<project/>') is not None
    # expat reads UTF-16 without a BOM from the declaration's first bytes, so only the
    # NUL check keeps its entity declaration, which the ASCII byte check cannot see, out
    utf16 = ('<?xml version="1.0"?><!DOCTYPE p [<!ENTITY x "y">]><project/>').encode(
        'utf-16-le'
    )
    assert safe_root(utf16) is None
    # expat reads windows-1252 too, so only the encoding check refuses the \x80
    windows_1252 = (
        b'<?xml version="1.0" encoding="windows-1252"?><project>\x80</project>'
    )
    assert safe_root(windows_1252) is None


def test_dependency_group_comes_from_the_dependencies():
    data = pom_xml(
        *GIT,
        dependencies=[
            ('org.jenkins-ci.plugins', 'mailer', '534.v1'),
            ('io.jenkins.plugins', 'caffeine-api', '3.2.4'),
        ],
    )
    assert dependency_group(data, 'caffeine-api') == 'io.jenkins.plugins'
    assert dependency_group(data, 'absent') is None


def test_dependency_group_resolves_the_projects_own_group():
    assert (
        dependency_group(
            pom_xml(*GIT, dependencies=[('${project.groupId}', 'git-client', '6.0')]),
            'git-client',
        )
        == 'org.jenkins-ci.plugins'
    )


def test_any_other_property_is_unknown():
    assert (
        dependency_group(pom_xml(*GIT, dependencies=[('${corp.group}', 'x', '1')]), 'x')
        is None
    )


def test_no_pom_or_not_xml_is_unknown():
    assert dependency_group(None, 'x') is None
    assert dependency_group(NGINX_404, 'x') is None


@pytest.mark.parametrize('pattern', [GROUP_ID, COORDINATE])
def test_the_coordinate_patterns_refuse_a_trailing_newline(pattern):
    assert pattern.match('org.jenkins-ci.plugins')
    assert not pattern.match('org.jenkins-ci.plugins\n')
