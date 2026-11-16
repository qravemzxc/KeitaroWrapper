"""Offer share (weight) math.

Keitaro requires the shares of offers inside one stream to add up to 100.
When an offer is added or removed, the shares are split evenly; the remainder
of the integer division goes to the first offers, so 3 offers -> 34/33/33,
6 offers -> 17/17/17/17/16/16. This is the same split the Keitaro UI does.
"""


def even_shares(count: int) -> list[int]:
    if count <= 0:
        return []
    base, rest = divmod(100, count)
    return [base + 1 if i < rest else base for i in range(count)]


def validate_shares(shares: list[int]) -> None:
    if not shares:
        return
    if any(s < 0 or s > 100 for s in shares):
        raise ValueError("Каждая доля должна быть в диапазоне 0..100")
    if sum(shares) != 100:
        raise ValueError(f"Сумма долей должна быть 100%, сейчас {sum(shares)}%")
