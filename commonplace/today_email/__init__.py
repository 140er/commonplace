"""Opt-in email for new Today items, off unless the server has outgoing mail."""

import logging

from commonplace import mailer

from . import routes, schema
from .notifier import TodayEmail

logger = logging.getLogger(__name__)


def install(gateway):
    if getattr(gateway, "today_email", None) is not None:
        return
    schema.ensure(gateway.db)
    try:
        mail = mailer.from_env()
    except mailer.MailError:
        logger.warning("Today email is not configured")
        mail = mailer.DisabledMailer()
    notifier = gateway.today_email = TodayEmail(gateway, mail)
    routes.register(gateway)
    if notifier.available:
        notifier.start()
        gateway.db.add_pre_close_callback(notifier.stop)
