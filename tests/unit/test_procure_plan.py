"""Unit tests for uc.procure.plan: which POMs and cores a download run stages, and why."""

import pytest

from uc.doctor import DoctorReport
from uc.procure import ProcureError
from uc.procure.plan import check_war_args, plan_cores, plan_poms
from uc.procure.stager import Wanted

AD = 'org.jenkins-ci.plugins:active-directory:2.967.v1'
AD_POM = 'org/jenkins-ci/plugins/active-directory/2.967.v1/active-directory-2.967.v1.pom'
CAFFEINE_POM = 'io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4.pom'
POLARIS_POM = 'org/jenkins-ci/plugins/polaris/1.4.0/polaris-1.4.0.pom'
WAR = 'org/jenkins-ci/main/jenkins-war/{0}/jenkins-war-{0}.{1}'


def adapter(**lists):
    return DoctorReport(repo_adapter={name: lists.get(name, []) for name in ('derived_poms', 'replaced_poms', 'needs_pom', 'misplaced', 'skipped', 'dates_from_repository')})


def test_every_pom_the_adapter_names_is_planned_once_in_path_order():
    report = adapter(
        derived_poms=[{'gav': 'io.jenkins.plugins:caffeine-api:3.2.4', 'url': 'u'}, {'gav': AD, 'url': 'u'}],
        replaced_poms=[{'path': AD_POM, 'reason': 'not XML', 'url': 'u'}],
        needs_pom=[{'gav': 'org.jenkins-ci.plugins:polaris:1.4.0', 'url': 'u'}],
    )
    assert plan_poms(report) == [
        Wanted(CAFFEINE_POM, 'pom', 'real POM for io.jenkins.plugins:caffeine-api:3.2.4; the one derived from its archive works meanwhile'),
        Wanted(AD_POM, 'pom', 'replaces an unusable .pom (not XML): delete the one in the repository before pushing', replaces_unusable=True),
        Wanted(POLARIS_POM, 'pom', 'org.jenkins-ci.plugins:polaris:1.4.0 has no POM in the repository or its archive: the generator drops it without one'),
    ]


def test_without_the_adapter_every_hpi_without_a_pom_is_planned():
    report = DoctorReport(hpi_without_pom=['org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi'])
    assert plan_poms(report) == [Wanted('org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom', 'pom', 'no .pom beside org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi: the generator drops it without one')]


def test_explicit_wars_are_planned_with_their_poms():
    plan = plan_cores(['2.479.1'], DoctorReport(cores=['2.541.3']))
    assert plan.files == [Wanted(WAR.format('2.479.1', 'war'), 'core', 'requested'), Wanted(WAR.format('2.479.1', 'pom'), 'core', 'requested')]
    assert plan.notes == []


def test_a_war_already_in_the_repository_is_skipped_with_a_note():
    plan = plan_cores(['2.541.3'], DoctorReport(cores=['2.541.3']))
    assert plan.files == []
    assert plan.notes == ['skipped   jenkins-war 2.541.3  (already in the repository)']


def test_required_plans_the_missing_cores_above_the_floor():
    report = DoctorReport(cores=['2.462.3'], required_cores={'2.479.1': ['g:a:1', 'g:b:1']}, core_floor='2.462.3', missing_cores=[{'version': '2.479.1', 'url': 'u', 'required_by': 2, 'example': 'g:a:1'}])
    reason = 'required by 2 plugin releases, e.g. g:a:1'
    assert plan_cores(['required'], report).files == [Wanted(WAR.format('2.479.1', 'war'), 'core', reason), Wanted(WAR.format('2.479.1', 'pom'), 'core', reason)]


def test_required_says_so_when_nothing_is_missing():
    plan = plan_cores(['required'], DoctorReport(cores=['2.479.1'], required_cores={'2.479.1': ['g:a:1']}, core_floor='2.479.1'))
    assert plan.files == []
    assert plan.notes == ['no required core at or above 2.479.1 is missing']


def test_required_needs_a_floor_when_the_repository_has_no_core():
    with pytest.raises(ProcureError, match='add --since'):
        plan_cores(['required'], DoctorReport(required_cores={'2.479.1': ['g:a:1']}))


def test_required_is_refused_for_the_public_repository():
    with pytest.raises(ProcureError, match=r'public repo\.jenkins-ci\.org'):
        plan_cores(['required'], DoctorReport())


@pytest.mark.parametrize('value', ['2.479', '2.479.1', 'required'])
def test_war_arguments_accepted(value):
    assert check_war_args([value]) is None


@pytest.mark.parametrize('value', ['2.479.x', 'latest', '2.479.1-rc', '../2.479.1', ''])
def test_war_arguments_refused(value):
    with pytest.raises(ProcureError, match='--download-war takes a Jenkins version'):
        check_war_args([value])
