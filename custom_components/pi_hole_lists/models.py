"""Typed model for Pi-hole v6 adlists."""

from dataclasses import dataclass, fields, replace
from typing import Any, Self


@dataclass(frozen=True)
class PiHoleList:
    """A Pi-hole v6 adlist row (``GET /api/lists`` element).

    All fields except ``id`` are nullable: FTL output is trusted, so
    ``from_dict`` validates presence only where the integration depends on
    it (``id``, used as the coordinator key). Read-only FTL fields this
    integration does not use (``date_added``, ``date_modified``,
    ``abp_entries``) are deliberately not modeled and are dropped on parse.
    """

    id: int
    address: str | None = None
    enabled: bool | None = None
    type: str | None = None
    comment: str | None = None
    groups: list[int] | None = None
    number: int | None = None
    invalid_domains: int | None = None
    status: int | None = None
    date_updated: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Parse a list row, requiring only ``id``."""
        if "id" not in data:
            raise ValueError("Pi-hole list row is missing its id")
        names = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})

    def update_payload(self, enabled: bool) -> dict[str, Any]:
        """PUT body for this list.

        FTL's PUT is a full-row upsert: mutable fields absent from the body
        are reset (``comment`` -> NULL, ``enabled`` -> true). Always echo the
        comment so it survives the toggle; ``address``/``type`` ride in the
        URI/query and ``groups`` are only touched when the body carries a
        ``groups`` array, which we never send.
        """
        return {"enabled": enabled, "comment": self.comment}

    def merge_update(self, partial: Self) -> Self:
        """Return a copy with only the non-None fields of ``partial`` applied.

        Defensive: FTL answers a PUT with the full row, so in practice this is
        an identity — but a hypothetical slim response must not wipe details.
        """
        return replace(
            self,
            **{
                field.name: value
                for field in fields(self)
                if (value := getattr(partial, field.name)) is not None
            },
        )
