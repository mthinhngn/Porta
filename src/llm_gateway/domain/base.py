"""Shared behavior for external contract models."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Strict base model for stable wire contracts."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )
