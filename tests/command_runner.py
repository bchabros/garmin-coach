"""Recording command transport; a handler must recognize every accepted invocation."""

import subprocess


class RecordingRunner:
    """Record argv/options and refuse commands without an explicit handler response."""

    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []

    def __call__(self, arguments, **options):
        self.calls.append((list(arguments), dict(options)))
        result = self.handler(arguments, **options) if self.handler is not None else None
        if not isinstance(result, subprocess.CompletedProcess):
            raise AssertionError(f"Unexpected system command: {arguments!r}")
        if options.get("check"):
            result.check_returncode()
        return result
