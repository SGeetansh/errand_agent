"""In-memory event queue for asynchronous and simulated system events."""

from src.schemas import ProviderCancelledEvent


class EventQueue:
    """Non-blocking FIFO event queue supporting simulated proactive notifications."""

    def __init__(self) -> None:
        """Initialize empty event queue."""
        self._queue: list[ProviderCancelledEvent] = []

    def push(self, event: ProviderCancelledEvent) -> None:
        """Enqueue a new proactive cancellation event.

        Args:
            event: Event detailing provider cancellation.
        """
        self._queue.append(event)

    def poll(self) -> ProviderCancelledEvent | None:
        """Dequeue the next event in FIFO order, or None if empty.

        Returns:
            ProviderCancelledEvent | None: Next event or None.
        """
        return self._queue.pop(0) if self._queue else None

    def has_events(self) -> bool:
        """Check if any events are currently pending.

        Returns:
            bool: True if queue has items.
        """
        return len(self._queue) > 0
