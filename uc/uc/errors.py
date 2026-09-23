"""Exception types shared by the uc CLI. Every expected failure is a UcError subclass."""


class UcError(Exception):
    """An expected failure: the CLI prints the message and exits 1."""


class ConfigError(UcError):
    """The environment does not describe a valid configuration."""


def public_summary(exc: BaseException) -> str:
    """What /status.json may say about a failure: the class, never the message (spec §8.4).

    The full text reaches the container log, since generate() re-raises.
    """
    return f'{type(exc).__name__}: see the generator log'
