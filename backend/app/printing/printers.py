"""Printer profiles.

Printer configuration is deliberately independent of material configuration:
adding a machine must never require touching the material database and vice
versa. Anything printer specific (build volume, nozzles, enclosure) is read
from here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.errors import PrinterNotFoundError
from app.schemas import Printer

DATA_FILE = Path(__file__).parent / "data" / "printers.json"


@lru_cache(maxsize=1)
def all_printers() -> list[Printer]:
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [Printer(**entry) for entry in data["printers"]]


def get_printer(printer_id: str) -> Printer:
    for printer in all_printers():
        if printer.id == printer_id:
            return printer.model_copy(deep=True)
    raise PrinterNotFoundError(f"Printer profile '{printer_id}' is not available.")


def default_printer() -> Printer:
    return all_printers()[0].model_copy(deep=True)
