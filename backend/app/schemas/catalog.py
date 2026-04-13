from pydantic import BaseModel


class CatalogField(BaseModel):
    name: str
    label: str
    type: str  # "text" | "number" | "select"
    default: str | int | None = None
    options: list[str] | None = None  # for "select" type


class CatalogTemplate(BaseModel):
    id: str
    name: str
    description: str
    icon: str  # emoji or icon name
    category: str
    fields: list[CatalogField]
    image_path: str | None = None  # Optional custom icon image path
    enabled: bool = True
