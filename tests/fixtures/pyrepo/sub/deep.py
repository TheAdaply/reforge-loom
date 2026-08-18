"""Relative imports, twin top-level defs, and a function-local shadowing import."""

from datetime import timedelta

from . import sibling
from ..up import deep


class Digger:
    def dig(self):
        return sibling.probe()


def window():
    return timedelta(days=1)


def twin():
    return deep(1)


def twin():
    from datetime import timedelta

    return timedelta(hours=2)
