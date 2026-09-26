"""Money arithmetic for settlement.

Every monetary value in this system is a ``Decimal`` quantised to two places
with ROUND_HALF_UP, the convention finance systems use (Python's default
``ROUND_HALF_EVEN`` would round 0.005 down to 0.00 and quietly disagree with
the merchant's statement).
"""

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def quantize_money(value: Decimal) -> Decimal:
    """Round to two decimal places, half away from zero."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def commission_for(total: Decimal, rate: Decimal) -> Decimal:
    """Platform commission owed on ``total``.

    ``rate`` is a fraction: 0.02 means 2%. The product is quantised once, here,
    and never re-rounded downstream, so batch totals always reconcile.
    """
    return quantize_money(Decimal(total) * Decimal(rate))


def net_payout_for(total: Decimal, commission: Decimal) -> Decimal:
    """What the merchant actually receives: gross minus commission."""
    return quantize_money(Decimal(total) - Decimal(commission))
