"""Exception types shared by the uc CLI. Every expected failure is a UcError subclass."""


class UcError(Exception):
    """An expected failure: the CLI prints the message and exits 1."""


class ConfigError(UcError):
    """The environment does not describe a valid configuration."""
