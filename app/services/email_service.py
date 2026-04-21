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


def _send(to_email: str, subject: str, html: str) -> None:
    """Internal helper: send any HTML email via SendGrid."""
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


def send_ticket_created_email(
    professor_email: str,
    professor_name: str,
    student_name: str,
    ta_name: str,
    ticket_title: str,
    ticket_description: str,
) -> None:
    """Notify the professor that a new action ticket has been raised by a TA."""
    subject = f"New action ticket from {ta_name}: {ticket_title}"
    html = f"""\
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color: #1e1e1e; margin-bottom: 8px;">New Action Ticket</h2>
      <p style="color: #555; font-size: 15px; line-height: 1.5;">
        <strong>{ta_name}</strong> raised a ticket from their meeting with <strong>{student_name}</strong>
        that requires your attention.
      </p>
      <div style="background: #f8f8ff; border-left: 4px solid #4f46e5; border-radius: 4px; padding: 16px; margin: 20px 0;">
        <p style="margin: 0 0 6px; font-size: 14px; font-weight: 600; color: #1e1e1e;">{ticket_title}</p>
        <p style="margin: 0; font-size: 14px; color: #555; line-height: 1.5;">{ticket_description}</p>
      </div>
      <p style="color: #555; font-size: 14px;">Log in to Scheduler to review and update the ticket status.</p>
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
      <p style="color: #bbb; font-size: 12px;">Scheduler — AI-Driven Meeting Coordination</p>
    </div>
    """
    _send(professor_email, subject, html)


def send_professor_meeting_request_email(
    ta_email: str,
    ta_name: str,
    professor_name: str,
    student_name: str,
    reason: str,
) -> None:
    """Notify a TA that the professor has requested they schedule a meeting with a student."""
    subject = f"[Urgent] {professor_name} has requested a meeting with {student_name}"
    html = f"""\
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color: #1e1e1e; margin-bottom: 8px;">Meeting Requested by Professor</h2>
      <p style="color: #555; font-size: 15px; line-height: 1.5;">
        <strong>{professor_name}</strong> has requested that you schedule a meeting with
        <strong>{student_name}</strong> as soon as possible.
      </p>
      <div style="background: #fff7ed; border-left: 4px solid #f97316; border-radius: 4px; padding: 16px; margin: 20px 0;">
        <p style="margin: 0 0 4px; font-size: 13px; font-weight: 600; color: #9a3412;">Reason</p>
        <p style="margin: 0; font-size: 14px; color: #555; line-height: 1.5;">{reason}</p>
      </div>
      <p style="color: #555; font-size: 14px;">This request has been placed in your meeting queue with high priority. Please log in to Scheduler to book a slot.</p>
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
      <p style="color: #bbb; font-size: 12px;">Scheduler — AI-Driven Meeting Coordination</p>
    </div>
    """
    _send(ta_email, subject, html)


def send_ticket_notification_email(
    to_email: str,
    recipient_name: str,
    role: str,
    student_name: str,
    ta_name: str,
    ticket_title: str,
    new_status: str,
    resolution_note: str | None,
) -> None:
    """Notify TA, student, or professor that a ticket status has changed."""
    status_label = new_status.replace("_", " ").title()
    status_color = {"OPEN": "#f59e0b", "IN_PROGRESS": "#3b82f6", "RESOLVED": "#10b981"}.get(new_status, "#6b7280")

    note_block = ""
    if resolution_note:
        note_block = f"""\
      <div style="background: #f0fdf4; border-left: 4px solid #10b981; border-radius: 4px; padding: 14px; margin: 16px 0;">
        <p style="margin: 0 0 4px; font-size: 13px; font-weight: 600; color: #065f46;">Resolution note</p>
        <p style="margin: 0; font-size: 14px; color: #555;">{resolution_note}</p>
      </div>"""

    if role == "student":
        context = f"A ticket about your meeting with <strong>{ta_name}</strong> has been updated."
    elif role == "ta":
        context = f"Your ticket for student <strong>{student_name}</strong> has been updated."
    else:
        context = f"Ticket for <strong>{student_name}</strong> (via {ta_name}) has been updated."

    subject = f"Ticket '{ticket_title}' is now {status_label}"
    html = f"""\
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color: #1e1e1e; margin-bottom: 8px;">Ticket Update</h2>
      <p style="color: #555; font-size: 15px; line-height: 1.5;">Hi {recipient_name}, {context}</p>
      <div style="background: #f8f8ff; border-left: 4px solid #4f46e5; border-radius: 4px; padding: 16px; margin: 20px 0;">
        <p style="margin: 0 0 8px; font-size: 14px; font-weight: 600; color: #1e1e1e;">{ticket_title}</p>
        <p style="margin: 0; font-size: 13px;">
          Status: <span style="font-weight: 600; color: {status_color};">{status_label}</span>
        </p>
      </div>
      {note_block}
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
      <p style="color: #bbb; font-size: 12px;">Scheduler — AI-Driven Meeting Coordination</p>
    </div>
    """
    _send(to_email, subject, html)
