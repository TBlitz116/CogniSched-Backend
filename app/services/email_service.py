import httpx
from app.core.config import settings

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


def send_invite_email(to_email: str, invite_url: str, inviter_name: str, role: str):
    """Send an invite email via SendGrid."""
    role_label = "Teaching Assistant" if role == "TA" else "Student"

    subject = f"You're invited to join Scheduler as a {role_label}"

    html = f"""\
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color: #1e1e1e; margin-bottom: 8px;">You've been invited!</h2>
      <p style="color: #555; font-size: 15px; line-height: 1.5;">
        <strong>{inviter_name}</strong> has invited you to join <strong>Scheduler</strong> as a <strong>{role_label}</strong>.
      </p>
      <p style="color: #555; font-size: 15px; line-height: 1.5;">
        Click the button below to accept your invitation and set up your account.
      </p>
      <a href="{invite_url}"
         style="display: inline-block; background: #4f46e5; color: #fff; text-decoration: none;
                padding: 12px 28px; border-radius: 8px; font-size: 15px; font-weight: 600; margin: 20px 0;">
        Accept Invitation
      </a>
      <p style="color: #999; font-size: 13px; margin-top: 24px;">
        This invite expires in 7 days. If you didn't expect this email, you can safely ignore it.
      </p>
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
      <p style="color: #bbb; font-size: 12px;">Scheduler — AI-Driven Meeting Coordination</p>
    </div>
    """

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "Scheduler"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }

    response = httpx.post(
        SENDGRID_API_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    if response.status_code not in (200, 201, 202):
        raise Exception(f"SendGrid error {response.status_code}: {response.text}")
