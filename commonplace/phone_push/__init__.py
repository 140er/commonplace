"""Opt-in phone notification extension."""


def install(gateway):
    gateway.phone_push = None
    return


def stop(gateway):
    return
