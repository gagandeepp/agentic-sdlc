"""Stock levels for a small shop."""

from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    units: int
    reorder_level: int


def restock(item: Item, units: int) -> Item:
    if units <= 0:
        raise ValueError("units must be positive")
    return Item(item.sku, item.name, item.units + units, item.reorder_level)


def total_units(items: list[Item]) -> int:
    return sum(item.units for item in items)
