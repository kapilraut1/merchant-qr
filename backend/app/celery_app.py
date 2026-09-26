"""Celery application.

The API process does not run tasks; the ``worker`` and ``beat`` containers do.
``include`` makes sure the task modules are registered when the worker boots.
"""

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "merchant_settlement",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.settlement"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A nightly batch that takes longer than this should be investigated, not
    # silently retried into a duplicate batch.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=60 * 60 * 24,
    beat_schedule={
        "nightly-merchant-settlement": {
            "task": "settlement.settle_all_merchants",
            # 00:05 UTC: settles the previous day, which is complete by then.
            "schedule": crontab(hour=0, minute=5),
        },
    },
)
