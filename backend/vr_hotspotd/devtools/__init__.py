"""Dev Bridge managed developer-tools metadata and read-only status.

This package holds the reviewed Platform-Tools pin metadata
(``platform_tools_pin.json``) and the read-only status/discovery code built
on top of it. Nothing in this package downloads, installs, removes, or
executes anything; installer/downloader behavior is intentionally absent and
remains blocked by the pin's ``implementation_blocked`` flag.
"""
