import zoneinfo

# Time zone rules from the tzdata package, never the OS: runners and laptops lag rule changes.
zoneinfo.reset_tzpath([])
