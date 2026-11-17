"""Offer share (weight) math.

Keitaro requires the shares of offers inside one stream to add up to 100.
When an offer is added or removed, the shares are split evenly; the remainder
of the integer division goes to the first offers, so 3 offers -> 34/33/33,
6 offers -> 17/17/17/17/16/16. This is the same split the Keitaro UI does.
"""


def even_shares(count: int, total: int = 100) -> list[int]:
    if count <= 0:
        return []
    base, rest = divmod(total, count)
    return [base + 1 if i < rest else base for i in range(count)]


def shares_with_pins(offer_ids: list[int], pinned: dict[int, int]) -> list[int]:
    """Shares for offer_ids (in order). Pinned offers keep their share; the rest of
    100% is split evenly between the other offers (remainder to the first ones)."""
    pinned = {oid: share for oid, share in pinned.items() if oid in offer_ids}
    free = [oid for oid in offer_ids if oid not in pinned]
    rest = 100 - sum(pinned.values())
    if not free:
        if rest != 0:
            raise ValueError(
                f"Все офферы закреплены, их доли в сумме {100 - rest}%, а нужно 100%. Открепите один из офферов."
            )
        return [pinned[oid] for oid in offer_ids]
    if rest < len(free):
        raise ValueError(
            f"Закреплённые офферы занимают {100 - rest}%: остальным {len(free)} офферам "
            f"не хватает доли (нужно хотя бы по 1%). Открепите один из офферов."
        )
    free_shares = dict(zip(free, even_shares(len(free), rest)))
    return [pinned[oid] if oid in pinned else free_shares[oid] for oid in offer_ids]


def validate_shares(shares: list[int]) -> None:
    if not shares:
        return
    if any(s < 0 or s > 100 for s in shares):
        raise ValueError("Каждая доля должна быть в диапазоне 0..100")
    if sum(shares) != 100:
        raise ValueError(f"Сумма долей должна быть 100%, сейчас {sum(shares)}%")
