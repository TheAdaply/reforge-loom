"""Top of the fixture package — the target of ``from ..up import deep``."""


def deep(value):
    return value * 2


def surface():
    return deep(1)
