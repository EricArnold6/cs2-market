"""Module 4 — Alerting & Orchestration.

DingTalk Webhook notification engine for CS2 market anomaly alerts.
"""

from .bot import DingTalkAlerter
from .dispatcher import AlertDispatcher

__all__ = ["DingTalkAlerter", "AlertDispatcher"]
