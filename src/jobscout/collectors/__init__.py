"""Collector registry."""

from jobscout.collectors.ashby import AshbyCollector
from jobscout.collectors.greenhouse import GreenhouseCollector
from jobscout.collectors.lever import LeverCollector
from jobscout.collectors.website import WebsiteCollector
from jobscout.domain import Provider


def build_collectors(attempts: int = 2):
    return {
        Provider.GREENHOUSE: GreenhouseCollector(attempts=attempts),
        Provider.LEVER: LeverCollector(attempts=attempts),
        Provider.ASHBY: AshbyCollector(attempts=attempts),
        Provider.WEBSITE: WebsiteCollector(attempts=attempts),
    }


__all__ = [
    "AshbyCollector",
    "GreenhouseCollector",
    "LeverCollector",
    "WebsiteCollector",
    "build_collectors",
]
