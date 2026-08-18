"""Leaf module of the second fixture repo — the CALLS target for `svc.py::Warehouse/restock`.

Two files is the whole repo (MULTIREPO-SPEC §6): enough for a File->File IMPORTS edge and
a cross-file CALLS edge, small enough that a boot-time index costs nothing.
"""


def post_entry(sku):
    return {"sku": sku}


def audit_trail():
    return []
