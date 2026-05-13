# main.py
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from config import PORT, SERVER_URL
from database import init_db
from scheduler_engine import start_scheduler, shutdown_scheduler, reload_scheduler

# Modular Route Imports
from api_routes import api_router
from page_routes import page_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the FastAPI application."""
    init_db()
    start_scheduler()
    reload_scheduler()
    yield
    shutdown_scheduler()

# Initialize Fast & Scalable Application
app = FastAPI(title="Quantamental Dashboard", lifespan=lifespan)

# Mount Static Directories
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount Logical Sub-Routers
app.include_router(api_router)
app.include_router(page_router)


if __name__ == "__main__":
    print(f"Starting Quantamental Web Server at {SERVER_URL}:{PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)