import pytest

from inventory import Item, restock, total_units


def test_restock_adds_units() -> None:
    assert restock(Item("A1", "Widget", 2, 5), 3).units == 5


def test_restock_rejects_non_positive_units() -> None:
    with pytest.raises(ValueError):
        restock(Item("A1", "Widget", 2, 5), 0)


def test_total_units() -> None:
    assert total_units([Item("A1", "W", 2, 5), Item("B2", "G", 3, 1)]) == 5
