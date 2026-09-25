"""Console + optional Telegram notifications (stdlib only)."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("lazada_bot")


class Notifier:
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, message: str) -> bool:
        """Returns True if the Telegram message was delivered."""
        log.warning("NOTIFY: %s", message)
        if not (self.bot_token and self.chat_id):
            return False
        body = json.dumps({"chat_id": self.chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
            return True
        except Exception as e:  # a failed alert must not kill the bot
            log.error("Telegram send failed: %s", e)
            return False
