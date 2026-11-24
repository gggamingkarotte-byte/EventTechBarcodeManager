from dataclasses import dataclass
from typing import Optional


@dataclass
class Article:
    id: Optional[int] = None
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    category: str = ""
    barcode: str = ""
    quantity: int = 1
    box_id: Optional[int] = None
    description: str = ""
    technical_data: str = ""
    status: str = "Einsatzbereit"
