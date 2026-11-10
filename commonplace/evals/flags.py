"""Stub."""


class Scanner:
    def __init__(self, scan, version):
        self._scan = scan
        self.version = version

    def codes(self, strings):
        return []


def load():
    return Scanner(lambda text: [], "0" * 16)


def run_strings(rows, artifacts, manifest):
    return iter(())
