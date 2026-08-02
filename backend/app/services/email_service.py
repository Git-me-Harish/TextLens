"""
Email notification service — Resend API.

Free tier limits (respected automatically):
  3,000 emails/month  |  100 emails/day

All functions are synchronous — called from Celery tasks (sync context).
Errors are logged but never raised so task completion is never blocked.

Setup:
  RESEND_API_KEY=re_...      in .env
  FROM_EMAIL=notify@yourdomain.com  (must be a Resend-verified domain)
  For local dev, leave FROM_EMAIL as default — Resend's sandbox domain
  works without verification but only delivers to the account owner.
"""
import logging
from typing import Optional

import resend
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


def _init() -> bool:
    """Lazy-configure Resend API key. Returns False if not configured."""
    if not settings.RESEND_API_KEY:
        logger.debug("email.not_configured", hint="Set RESEND_API_KEY to enable email notifications")
        return False
    resend.api_key = settings.RESEND_API_KEY
    return True


# Email templates 
def _job_completed_html(
    user_name: str,
    filename: str,
    job_type: str,
    processing_time_ms: Optional[int],
    status: str,
    error_message: Optional[str] = None,
) -> str:
    status_color = "#16a34a" if status == "completed" else "#dc2626"
    status_label = "✓ Completed" if status == "completed" else "✗ Failed"
    time_str = f"{processing_time_ms:,}ms" if processing_time_ms else "—"

    body = f"""
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">File</td>
        <td style="padding:6px 0;font-weight:600;font-size:13px">{filename}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Job type</td>
        <td style="padding:6px 0;font-size:13px">{job_type.replace("_"," ").title()}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Processing time</td>
        <td style="padding:6px 0;font-size:13px">{time_str}</td></tr>
    """
    if error_message:
        body += f"""
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Error</td>
        <td style="padding:6px 0;font-size:13px;color:#dc2626">{error_message}</td></tr>"""

    return _wrap_template(
        user_name=user_name,
        title="Extraction complete",
        status_label=status_label,
        status_color=status_color,
        rows_html=body,
        cta_label="View results in TextLens",
        cta_url=f"{settings.FRONTEND_URL}/history",
    )


def _agent_completed_html(
    user_name: str,
    filename: str,
    domain: str,
    pipeline_type: str,
    confidence_score: Optional[int],
    processing_time_ms: Optional[int],
    status: str,
    error_message: Optional[str] = None,
) -> str:
    status_color = "#16a34a" if status == "completed" else "#dc2626"
    status_label = "✓ Completed" if status == "completed" else "✗ Failed"
    conf_str = f"{confidence_score}%" if confidence_score is not None else "—"
    time_str = f"{processing_time_ms:,}ms" if processing_time_ms else "—"

    body = f"""
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">File</td>
        <td style="padding:6px 0;font-weight:600;font-size:13px">{filename}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Domain</td>
        <td style="padding:6px 0;font-size:13px">{domain.title()}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Pipeline</td>
        <td style="padding:6px 0;font-size:13px">{pipeline_type.replace("_"," ").title()}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Confidence</td>
        <td style="padding:6px 0;font-size:13px">{conf_str}</td></tr>
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Processing time</td>
        <td style="padding:6px 0;font-size:13px">{time_str}</td></tr>
    """
    if error_message:
        body += f"""
    <tr><td style="padding:6px 0;color:#64748b;font-size:13px">Error</td>
        <td style="padding:6px 0;font-size:13px;color:#dc2626">{error_message}</td></tr>"""

    return _wrap_template(
        user_name=user_name,
        title="Pipeline analysis complete",
        status_label=status_label,
        status_color=status_color,
        rows_html=body,
        cta_label="View pipeline results",
        cta_url=f"{settings.FRONTEND_URL}/agent-history",
    )


def _wrap_template(
    user_name: str,
    title: str,
    status_label: str,
    status_color: str,
    rows_html: str,
    cta_label: str,
    cta_url: str,
) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden">

        <!-- Header -->
        <tr><td style="padding:28px 36px;background:#0f172a">
          <span style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.5px">TextLens</span>
          <span style="color:#94a3b8;font-size:13px;margin-left:12px">Document Intelligence</span>
        </td></tr>

        <!-- Status badge -->
        <tr><td style="padding:28px 36px 0">
          <div style="display:inline-block;background:{status_color}18;color:{status_color};border:1px solid {status_color}40;border-radius:99px;padding:4px 14px;font-size:12px;font-weight:700;letter-spacing:0.05em">
            {status_label}
          </div>
          <h2 style="margin:12px 0 4px;font-size:20px;font-weight:700;color:#0f172a">{title}</h2>
          <p style="margin:0;color:#64748b;font-size:14px">Hi {user_name}, here's a summary of your job.</p>
        </td></tr>

        <!-- Details table -->
        <tr><td style="padding:20px 36px">
          <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e2e8f0">
            {rows_html}
          </table>
        </td></tr>

        <!-- CTA -->
        <tr><td style="padding:0 36px 32px">
          <a href="{cta_url}" style="display:inline-block;background:#6366f1;color:#ffffff;text-decoration:none;border-radius:8px;padding:10px 24px;font-size:14px;font-weight:600">
            {cta_label} →
          </a>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:20px 36px;background:#f8fafc;border-top:1px solid #e2e8f0">
          <p style="margin:0;color:#94a3b8;font-size:12px">
            You're receiving this because you have job notifications enabled in TextLens.
            <br>To unsubscribe, update your notification preferences in your profile settings.
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# Public API 
def send_job_notification(
    to_email: str,
    user_name: str,
    filename: str,
    job_type: str,
    status: str,
    processing_time_ms: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Send OCR job completion email. Synchronous — safe for Celery tasks."""
    if not _init():
        return

    subject = (
        f"✓ Extraction complete — {filename}"
        if status == "completed"
        else f"✗ Extraction failed — {filename}"
    )

    try:
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": _job_completed_html(
                user_name, filename, job_type,
                processing_time_ms, status, error_message,
            ),
        })
        logger.info("email.sent", to=to_email, subject=subject[:50])
    except Exception as exc:
        logger.error("email.send_failed", error=str(exc), to=to_email)


def send_agent_notification(
    to_email: str,
    user_name: str,
    filename: str,
    domain: str,
    pipeline_type: str,
    status: str,
    confidence_score: Optional[int] = None,
    processing_time_ms: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Send agent pipeline completion email. Synchronous — safe for Celery tasks."""
    if not _init():
        return

    subject = (
        f"✓ Pipeline complete — {pipeline_type.replace('_',' ').title()}"
        if status == "completed"
        else f"✗ Pipeline failed — {filename}"
    )

    try:
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": _agent_completed_html(
                user_name, filename, domain, pipeline_type,
                confidence_score, processing_time_ms, status, error_message,
            ),
        })
        logger.info("email.sent", to=to_email, subject=subject[:50])
    except Exception as exc:
        logger.error("email.send_failed", error=str(exc), to=to_email)