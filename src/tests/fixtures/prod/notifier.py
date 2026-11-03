"""Production module: notifications (fixture prod code)."""


class Notifier:
    """Sends notifications and keeps an internal send queue."""

    def __init__(self) -> None:
        self._sent: list = []

    def notify(self, to: str, body: str) -> str:
        message = f"to={to};body={body}"
        self._sent.append(message)
        return message

    def _queue(self) -> list:
        """Private accessor — implementation detail (§5.11)."""
        return self._sent
