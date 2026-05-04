# v2
import sys
import os

# Ensure the project root is on the path so `app` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mangum import Mangum
from app.web import app  # noqa: F401 — re-exported for Vercel

handler = Mangum(app, lifespan="off")
