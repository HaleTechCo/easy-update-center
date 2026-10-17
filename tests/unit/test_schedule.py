"""Unit tests for uc.schedule.

The crontab, and the first run before handing over to supercronic.
"""

import pytest

from uc.schedule import ScheduleError, crontab_text, job_line, run_schedule
from ucsupport import make_settings


def test_job_line():
    assert (
        job_line('0 */6 * * *', 'GENERATE_SCHEDULE', 'uc generate --wait')
        == '0 */6 * * * uc generate --wait\n'
    )
    assert (
        job_line('@hourly', 'GENERATE_SCHEDULE', 'uc generate --wait')
        == '@hourly uc generate --wait\n'
    )


def test_crontab_runs_generate_and_waits_for_the_lock(tmp_path, secrets_dir):
    assert (
        crontab_text(make_settings(tmp_path, secrets_dir))
        == '0 */6 * * * uc generate --wait\n'
    )


def test_crontab_adds_the_doctor_report_when_scheduled(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, DOCTOR_SCHEDULE='30 5 * * *')
    assert (
        crontab_text(settings)
        == '0 */6 * * * uc generate --wait\n30 5 * * * uc doctor --record\n'
    )


@pytest.mark.parametrize(
    ('name', 'value'),
    [('GENERATE_SCHEDULE', 'every six hours'), ('DOCTOR_SCHEDULE', 'nightly')],
)
def test_crontab_rejects_nonsense(tmp_path, secrets_dir, name, value):
    with pytest.raises(
        ScheduleError, match=f'{name} must be a 5-field cron expression or an @macro'
    ):
        crontab_text(make_settings(tmp_path, secrets_dir, **{name: value}))


def run(settings, tmp_path, generate_once):
    calls = []
    run_schedule(
        settings,
        generate_once=lambda: (calls.append('generate'), generate_once()),
        exec_=lambda _file, argv, _env: calls.append(argv),
        crontab=tmp_path / 'crontab',
    )
    return calls


def test_first_run_generates_then_hands_over_to_supercronic(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = run(settings, tmp_path, lambda: None)
    assert calls == [
        'generate',
        ['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')],
    ]
    assert (tmp_path / 'crontab').read_text() == '0 */6 * * * uc generate --wait\n'


def test_a_bad_schedule_stops_before_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, DOCTOR_SCHEDULE='nightly')
    calls = []
    with pytest.raises(ScheduleError):
        run_schedule(
            settings,
            generate_once=lambda: calls.append('generate'),
            exec_=lambda *_args: calls.append('exec'),
            crontab=tmp_path / 'crontab',
        )
    assert calls == []
    assert not (tmp_path / 'crontab').exists()


def test_existing_release_skips_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    (settings.data_dir / 'releases' / '20260922T000000Z').mkdir(parents=True)
    (settings.data_dir / 'live').symlink_to('releases/20260922T000000Z')
    assert run(settings, tmp_path, lambda: None) == [
        ['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')]
    ]


def test_failed_first_run_still_schedules(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)

    def boom():
        msg = 'generator crashed'
        raise RuntimeError(msg)

    assert run(settings, tmp_path, boom) == [
        'generate',
        ['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')],
    ]


def test_the_scheduler_hands_supercronic_its_environment(
    tmp_path, secrets_dir, monkeypatch
):
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy:3128')
    seen = []
    run_schedule(
        make_settings(tmp_path, secrets_dir),
        generate_once=lambda: None,
        exec_=lambda _file, _argv, env: seen.append(env),
        crontab=tmp_path / 'crontab',
    )
    assert seen[0]['HTTPS_PROXY'] == 'http://proxy:3128'
