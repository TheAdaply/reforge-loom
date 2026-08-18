"""The adversarial service module (conduit-verify §2.4 cases, all in one file).

Cases here: a TYPE_CHECKING-guarded import that MUST still emit an IMPORTS edge; a
decorator-wrapped ``async def`` whose qualname anchors to the inner function_definition;
a call site hiding in a parameter default (``Depends(get_db)`` shape); a ``@staticmethod``
inside a large class; a nested closure that must NOT be minted (its calls roll up to the
enclosing function); a class-body call that attributes to the Class node; a method guarded
under ``if TYPE_CHECKING:`` inside the class body that must NOT be minted; and a class
nested inside a class.
"""

from typing import TYPE_CHECKING

import lib.util as lu
from models import AuthResult
from up import deep

if TYPE_CHECKING:
    from sub.deep import Digger


def Depends(dep):
    return dep


def get_db():
    return "db"


def route(path):
    def wrap(fn):
        return fn

    return wrap


class AuthService:
    """Large class: staticmethod, guarded method, nested class, decorated async def."""

    default_tag = lu.make_tag()

    @staticmethod
    def salt():
        return "s"

    def authenticate(self, email, db=Depends(get_db)):
        token = lu.make_token(email)

        def _finish(value):
            return lu.helper(value)

        return _finish(token)

    @route("/verify")
    async def verify(self, raw):
        return AuthResult(self.salt())

    if TYPE_CHECKING:

        def typed_only(self) -> "Digger":
            ...

    class Session:
        def open(self):
            return AuthService.salt()


def bootstrap():
    svc = AuthService()
    return deep(svc)
