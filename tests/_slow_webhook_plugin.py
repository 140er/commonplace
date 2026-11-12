"""Pytest plugin: make every server worker start handling its request late.

A request worker sleeps well past the 50 ms client timeout before it reads
anything, as on a starved host, so a test that expects the server to have
received the request by the time the client gives up fails here.
"""

import socketserver
import time

DELAY_SECONDS = 0.5


def pytest_configure(config):
    original = socketserver.ThreadingMixIn.process_request_thread

    def late(self, request, client_address):
        time.sleep(DELAY_SECONDS)
        original(self, request, client_address)

    socketserver.ThreadingMixIn.process_request_thread = late
