"""Collector registry."""

from jobscout.collectors.ashby import AshbyCollector
from jobscout.collectors.greenhouse import GreenhouseCollector
from jobscout.collectors.job_board import JobBoardCollector
from jobscout.collectors.lever import LeverCollector
from jobscout.collectors.recruitee import RecruiteeCollector
from jobscout.collectors.smartrecruiters import SmartRecruitersCollector
from jobscout.collectors.website import WebsiteCollector
from jobscout.domain import Provider


def build_collectors(attempts: int = 2):
    return {
        Provider.GREENHOUSE: GreenhouseCollector(attempts=attempts),
        Provider.JOB_BOARD: JobBoardCollector(attempts=attempts),
        Provider.LEVER: LeverCollector(attempts=attempts),
        Provider.ASHBY: AshbyCollector(attempts=attempts),
        Provider.RECRUITEE: RecruiteeCollector(attempts=attempts),
        Provider.SMARTRECRUITERS: SmartRecruitersCollector(attempts=attempts),
        Provider.WEBSITE: WebsiteCollector(attempts=attempts),
    }


__all__ = [
    "AshbyCollector",
    "GreenhouseCollector",
    "JobBoardCollector",
    "LeverCollector",
    "RecruiteeCollector",
    "SmartRecruitersCollector",
    "WebsiteCollector",
    "build_collectors",
]
