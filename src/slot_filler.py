"""Slot schema and filling logic for errand requirements."""

import copy
from dataclasses import dataclass
from typing import Any


@dataclass
class ErrandSlots:
    """Slot values for an errand request. None indicates unsupplied.

    Attributes:
        service: Service category (plumber, ac_service, electrician).
        area: Geographical location or neighborhood.
        date_range: Requested date window or day.
        time_preference: Time preference (e.g. mornings only, after 10am).
        budget_max: Maximum budget in INR.
        provider_preference: Specific preference (e.g. same as last time).
        specific_issue: Specific task detail (e.g. kitchen tap leak).
    """

    service: str | None = None
    area: str | None = None
    date_range: str | None = None
    time_preference: str | None = None
    budget_max: float | None = None
    provider_preference: str | None = None
    specific_issue: str | None = None

    def missing_required(self) -> list[str]:
        """Return names of required fields that are not yet filled.

        Returns:
            list[str]: Missing required slot field names.
        """
        required = ["service", "area", "date_range"]
        return [f for f in required if getattr(self, f) is None]

    def is_complete(self) -> bool:
        """Check if all required slots are present.

        Returns:
            bool: True if all required slots are non-None.
        """
        return len(self.missing_required()) == 0

    def update(self, **kwargs: Any) -> "ErrandSlots":
        """Return new slots with provided kwargs updated, preserving other fields.

        Args:
            **kwargs: Slot field values to update if not None.

        Returns:
            ErrandSlots: Newly updated ErrandSlots copy.
        """
        new_slots = copy.copy(self)
        for key, val in kwargs.items():
            if hasattr(new_slots, key) and val is not None:
                setattr(new_slots, key, val)
        return new_slots


class SlotFiller:
    """Utility class to manage slot updates and field corrections."""

    @staticmethod
    def fill(current: ErrandSlots, updates: dict[str, Any]) -> ErrandSlots:
        """Update slots with non-None values from a dictionary.

        Args:
            current: Current slot values.
            updates: Dictionary of new candidate slot values.

        Returns:
            ErrandSlots: Updated ErrandSlots instance.
        """
        return current.update(**updates)

    @staticmethod
    def correct_field(
        current: ErrandSlots, field_name: str, new_value: Any
    ) -> ErrandSlots:
        """Correct a single field while preserving all unrelated slot values.

        Args:
            current: Current slot values.
            field_name: The name of the field to overwrite.
            new_value: New value for the field.

        Returns:
            ErrandSlots: Updated ErrandSlots instance.
        """
        return current.update(**{field_name: new_value})
