"""Unit tests for uc.pom: POM coordinates and the reasons a repository POM is unusable."""

import pytest

from archivefixture import pom_xml
from uc.pom import pom_coordinates, safe_root, unusable_reason

GIT = ('org.jenkins-ci.plugins', 'git', '5.2.0')
NGINX_404 = b'<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n'


def test_coordinates_of_a_real_pom():
    assert pom_coordinates(pom_xml(*GIT)) == GIT


def test_group_and_version_may_come_from_the_parent():
    data = b'<project><parent><groupId>org.jenkins-ci.plugins</groupId><artifactId>plugin</artifactId><version>5.2.0</version></parent><artifactId>git</artifactId></project>'
    assert pom_coordinates(data) == GIT


def test_a_usable_pom_has_no_reason():
    assert unusable_reason(pom_xml(*GIT), GIT) is None


@pytest.mark.parametrize(
    ('data', 'reason'),
    [
        (NGINX_404, 'not XML'),  # the saved error page from the operator's test repository
        (b'<html><body>404</body></html>', 'not a POM (root element <html>)'),
        (pom_xml('io.jenkins.plugins', 'git', '5.2.0'), 'declares io.jenkins.plugins:git:5.2.0, not org.jenkins-ci.plugins:git:5.2.0'),
        (b'<project><artifactId>git</artifactId></project>', 'does not declare groupId, artifactId and version'),
        (b'<!DOCTYPE p [<!ENTITY x "y">]><project/>', 'declares a DOCTYPE or entities'),
        (b'<project><version>${revision}${changelist}</version></project>', 'does not declare groupId, artifactId and version'),
    ],
)
def test_unusable_poms_say_why(data, reason):
    assert unusable_reason(data, GIT) == reason


def test_a_latin1_pom_is_usable():
    data = ('<?xml version="1.0" encoding="ISO-8859-1"?><project><groupId>org.jenkins-ci.plugins</groupId><artifactId>git</artifactId><version>5.2.0</version><name>Plugin by J\u00e9r\u00f4me</name></project>').encode('latin-1')
    assert unusable_reason(data, GIT) is None


def test_safe_root_refuses_what_the_byte_check_cannot_see():
    assert safe_root(b'<project/>') is not None
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE p [<!ENTITY x "y">]><project/>'.encode('utf-16')
    assert safe_root(utf16) is None  # a declaration the ASCII byte check would miss
    assert safe_root('<project/>'.encode('utf-16-le')) is None  # UTF-16 without a BOM is full of NULs
    assert safe_root(b'<project>\xff</project>') is None  # not UTF-8
