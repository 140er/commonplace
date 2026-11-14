"""Shared bounded numeric parsing for coworker HTTP routes."""

from .store import StoreError


def numeric(value, *, zero=False):
    if (
        not isinstance(value, str)
        or len(value) > 19
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise StoreError()
    number = int(value)
    if not (0 if zero else 1) <= number < 2**63:
        raise StoreError()
    return number
