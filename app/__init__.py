# BingeAlert - Main Application Package
# Single source of truth for the running version.
# Bump on each release tag; FastAPI(version=__version__) picks it up,
# and /api/version exposes it so the admin/login footers can render it.
__version__ = "2.2.2"
