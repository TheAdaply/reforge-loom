"""Second fixture repo — MULTIREPO-SPEC §6 integration ground.

The FILE NAME is deliberately the same as `pyrepo/svc.py`: `svc.py` is then a real File
node ref in BOTH repos, which is the only ref two repos can share while keeping every
symbol name distinct — and a shared ref is exactly what the salt-isolation property needs
(`ids.node_id` salts on `repo`, so the two `svc.py` File nodes must get different ids and
must never contend for the same claim).

Every SYMBOL below is absent from `pyrepo`, so a resolution that leaked across the repo
boundary would find nothing rather than silently matching the wrong repo's node.
"""

from ledger import post_entry


class Warehouse:
    """No name in this file appears anywhere in `tests/fixtures/pyrepo`."""

    def restock(self, sku):
        return post_entry(sku)


def dispatch(order):
    return Warehouse().restock(order)
