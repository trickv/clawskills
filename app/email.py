"""Email sending for ClawSkills."""

import os
import logging
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

# SMTP Configuration from environment
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
BASE_URL = os.getenv("BASE_URL", "https://clawskills.tech")


async def send_verification_email(to_email: str, verification_token: str, api_key: str) -> bool:
    """Send verification email with API key."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP not configured - cannot send email")
        return False
    
    verify_url = f"{BASE_URL}/verify/{verification_token}"
    
    subject = "Verify your ClawSkills API key"
    
    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #6366f1;">🔧 ClawSkills</h1>
        <p>Welcome! Click the link below to verify your email and activate your API key:</p>
        
        <p style="margin: 30px 0;">
            <a href="{verify_url}" style="background: #6366f1; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                Verify Email & Activate Key
            </a>
        </p>
        
        <p>Or copy this link: <code>{verify_url}</code></p>
        
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
        
        <p><strong>Your API Key (save this!):</strong></p>
        <p style="background: #f3f4f6; padding: 12px; border-radius: 6px; font-family: monospace; word-break: break-all;">
            {api_key}
        </p>
        
        <p style="color: #6b7280; font-size: 14px;">
            ⚠️ This key will only be shown once. Save it now!<br>
            The key won't work until you verify your email.
        </p>
        
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
        
        <p style="color: #6b7280; font-size: 12px;">
            This link expires in 24 hours.<br>
            If you didn't request this, you can ignore this email.
        </p>
    </body>
    </html>
    """
    
    text_body = f"""
ClawSkills - Verify Your API Key

Click this link to verify your email and activate your API key:
{verify_url}

Your API Key (save this!):
{api_key}

⚠️ This key will only be shown once. Save it now!
The key won't work until you verify your email.

This link expires in 24 hours.
If you didn't request this, you can ignore this email.
    """
    
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = to_email
    
    message.attach(MIMEText(text_body, "plain"))
    message.attach(MIMEText(html_body, "html"))
    
    try:
        await aiosmtplib.send(
            message,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info(f"Verification email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False
