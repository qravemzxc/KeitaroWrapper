import pytest

from app.shares import even_shares, validate_shares


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, [100]),
        (2, [50, 50]),
        (3, [34, 33, 33]),
        (6, [17, 17, 17, 17, 16, 16]),
        (7, [15, 15, 14, 14, 14, 14, 14]),
    ],
)
def test_even_shares(n, expected):
    assert even_shares(n) == expected
    assert sum(even_shares(n)) == 100


def test_even_shares_always_sum_to_100():
    for n in range(1, 101):
        shares = even_shares(n)
        assert sum(shares) == 100 and max(shares) - min(shares) <= 1


def test_validate_shares():
    validate_shares([70, 30])
    with pytest.raises(ValueError):
        validate_shares([70, 20])
    with pytest.raises(ValueError):
        validate_shares([120, -20])
