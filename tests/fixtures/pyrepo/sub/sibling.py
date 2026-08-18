"""A function-local import of a repo module plus a plain dotted ``import x.y as z``."""

import lib.util as lu


def probe():
    from up import surface

    return surface()


def blend(items):
    return lu.helper(items)
