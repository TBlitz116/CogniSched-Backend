from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import Base, engine
from app.models import user, invite, mapping, calendar, meeting, cognitive, faculty, approval, ticket, decision  # noqa
from app.api import auth, users, mappings, professor, student, ta, analytics, tickets, decisions

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Scheduler API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(mappings.router, prefix="/mappings", tags=["mappings"])
app.include_router(professor.router, prefix="/professor", tags=["professor"])
app.include_router(student.router, prefix="/requests", tags=["student"])
app.include_router(ta.router, prefix="/ta", tags=["ta"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
app.include_router(decisions.router, prefix="/decisions", tags=["decisions"])


@app.get("/health")
def health():
    return {"status": "ok"}
