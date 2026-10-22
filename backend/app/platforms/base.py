from abc import ABC, abstractmethod


class AdPlatform(ABC):
    """Connector interface. Each method returns the external (platform) id or data.
    Methods raise on API errors; the caller decides how to degrade."""
    name: str

    def __init__(self, creds: dict):
        self.c = creds    # this user's decrypted credentials

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def create_campaign(self, c: dict) -> str: ...

    @abstractmethod
    def set_status(self, external_id: str, status: str) -> None: ...

    @abstractmethod
    def set_budget(self, external_id: str, daily_budget: float) -> None: ...

    @abstractmethod
    def list_campaigns(self) -> list[dict]: ...
