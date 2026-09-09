from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import games, gameplan, health, parlays, performance, players, predictions, slip, systems
from app.config import settings
from app.db import create_all

app = FastAPI(title="Football Predictor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    create_all()


app.include_router(health.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(systems.router, prefix="/api")
app.include_router(gameplan.router, prefix="/api")
app.include_router(parlays.router, prefix="/api")
app.include_router(players.router, prefix="/api")
app.include_router(slip.router, prefix="/api")
