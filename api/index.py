"""Vercel's conventional FastAPI entrypoint.

Keeping this tiny adapter separate lets local, Render and Vercel deployments
share the same application object without duplicating authentication or data
handling code.
"""

from api.main import app
