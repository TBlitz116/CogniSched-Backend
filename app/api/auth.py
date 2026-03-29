import uuid
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.user import User, UserRole
from app.models.invite import PendingInvite
from app.models.mapping import RoleMapping
from app.models.faculty import VerifiedFaculty
from app.services.email_service import send_invite_email
from pydantic import BaseModel, EmailStr

router = APIRouter()

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


async def _exchange_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


async def _get_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


class GoogleLoginRequest(BaseModel):
    code: str
    redirect_uri: str
    invite_token: str | None = None


@router.post("/google")
async def google_login(body: GoogleLoginRequest, db: Session = Depends(get_db)):
    try:
        tokens = await _exchange_code(body.code, body.redirect_uri)
    except httpx.HTTPError:
        raise HTTPException(status_code=400, detail="Failed to exchange Google auth code")

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    try:
        userinfo = await _get_userinfo(access_token)
    except httpx.HTTPError:
        raise HTTPException(status_code=400, detail="Failed to fetch Google user info")

    email = userinfo["email"]
    name = userinfo.get("name", email)

    user = db.query(User).filter(User.email == email).first()

    if user:
        # Update refresh token if Google issued a new one
        if refresh_token:
            user.google_refresh_token = refresh_token
            db.commit()
        jwt = create_access_token({"sub": str(user.id), "role": user.role})
        return {"access_token": jwt, "role": user.role}

    # New user — determine role
    if body.invite_token:
        invite = db.query(PendingInvite).filter(
            PendingInvite.token == body.invite_token,
            PendingInvite.used == False,
            PendingInvite.expires_at > datetime.utcnow(),
        ).first()
        if not invite:
            raise HTTPException(status_code=400, detail="Invalid or expired invite")

        role = invite.role_to_assign
        inviter = db.query(User).filter(User.id == invite.inviter_id).first()

        user = User(email=email, name=name, role=role, google_refresh_token=refresh_token)
        db.add(user)
        db.flush()

        if role == UserRole.TA:
            mapping = RoleMapping(ta_id=user.id, professor_id=inviter.id)
        else:
            professor_mapping = db.query(RoleMapping).filter(
                RoleMapping.ta_id == inviter.id,
                RoleMapping.student_id == None,
            ).first()
            mapping = RoleMapping(
                student_id=user.id,
                ta_id=inviter.id,
                professor_id=professor_mapping.professor_id if professor_mapping else inviter.id,
            )
        db.add(mapping)
        invite.used = True
        db.commit()
        db.refresh(user)

    else:
        # TODO: Re-enable faculty verification once scraper is run
        # For now, first user becomes professor; others need an invite
        existing_count = db.query(User).count()
        if existing_count > 0:
            raise HTTPException(
                status_code=400,
                detail="An invite link is required to join this platform",
            )
        user = User(email=email, name=name, role=UserRole.PROFESSOR, google_refresh_token=refresh_token)
        db.add(user)
        db.commit()
        db.refresh(user)

    jwt = create_access_token({"sub": str(user.id), "role": user.role})
    return {"access_token": jwt, "role": user.role}


class InviteRequest(BaseModel):
    email: EmailStr


@router.post("/invite")
def create_invite(
    body: InviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(__import__("app.api.deps", fromlist=["get_current_user"]).get_current_user),
):
    if current_user.role == UserRole.STUDENT:
        raise HTTPException(status_code=403, detail="Students cannot send invites")

    role_to_assign = UserRole.TA if current_user.role == UserRole.PROFESSOR else UserRole.STUDENT

    invite = PendingInvite(
        token=str(uuid.uuid4()),
        inviter_id=current_user.id,
        role_to_assign=role_to_assign,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    invite_url = f"{settings.FRONTEND_URL}/join?token={invite.token}"

    try:
        send_invite_email(
            to_email=body.email,
            invite_url=invite_url,
            inviter_name=current_user.name,
            role="TA" if role_to_assign == UserRole.TA else "Student",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invite created but email failed to send: {str(e)}")

    return {"invite_url": invite_url, "role_to_assign": role_to_assign, "expires_at": invite.expires_at, "sent_to": body.email}
