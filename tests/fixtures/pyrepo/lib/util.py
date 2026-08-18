"""Leaf helpers, reached through ``import lib.util as lu`` and ``from lib.util import ...``."""


def helper(name):
    return name.strip()


def make_tag():
    return "tag"


def make_token(seed):
    return helper(seed)
