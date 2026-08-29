from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.config import get_settings
from app.multimodal import validation
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


@app.middleware("http")
async def _reject_oversized_uploads(request: Request, call_next):
    """Rejects an oversized upload from its Content-Length, BEFORE Starlette
    parses the multipart body.

    This exists because the in-handler check cannot do it: FastAPI resolves an
    UploadFile parameter by parsing the entire body first, spooling it to disk,
    and it does so before the auth dependency runs -- so without this, an
    unauthenticated caller could make the server buffer an arbitrarily large
    file just by POSTing to the URL.

    Not complete on its own: a chunked request sends no Content-Length, and a
    lying one is caught later by the in-handler cap. Defence in depth, with the
    cheap check first."""
    if request.method == "POST" and request.url.path.endswith("/attachments"):
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > validation.MAX_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"file exceeds the {validation.MAX_BYTES // (1024 * 1024)} MB limit"},
            )
    return await call_next(request)


app.include_router(admin_router)
app.include_router(attachments_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(notifications_router)
app.include_router(tickets_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
