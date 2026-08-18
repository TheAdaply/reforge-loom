"""A class-body call site: it attributes to the Class node, never to the File node."""

from lib.util import make_tag


class AuthResult:
    """Field initialiser below is a call in the class body (§4 bucketing)."""

    tag = make_tag()

    def token(self):
        return self.tag
