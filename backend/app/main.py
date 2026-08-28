from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.config import get_settings
from app.multimodal.router import router as attachments_router
from app.notifications.router import router as notifications_router
from app.tickets.router import router as tickets_router

settings = get_settings()

app = FastAPI(title="Agentic Helpdesk")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(attachments_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(notifications_router)
app.include_router(tickets_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
