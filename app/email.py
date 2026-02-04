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


async def send_verification_code_email(to_email: str, verification_code: str) -> bool:
    """Send verification email with a short code (for agent-driven flow)."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP not configured - cannot send email")
        return False
    
    subject = f"ClawSkills Verification Code: {verification_code}"
    
    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #6366f1;">🔧 ClawSkills</h1>
        <p>Your AI agent is requesting a ClawSkills API key. Give this verification code to your agent:</p>
        
        <div style="background: #f3f4f6; padding: 24px; border-radius: 8px; text-align: center; margin: 24px 0;">
            <p style="font-size: 32px; font-family: monospace; font-weight: bold; letter-spacing: 2px; margin: 0; color: #1f2937;">
                {verification_code}
            </p>
        </div>
        
        <p style="color: #6b7280;">
            Copy this code and paste it into your chat with your AI agent.<br>
            Your agent will handle the rest!
        </p>
        
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
        
        <p style="color: #6b7280; font-size: 12px;">
            This code expires in 24 hours.<br>
            If you didn't request this, you can ignore this email.<br><br>
            <a href="https://clawskills.tech">ClawSkills</a> - AI Agent Skill Registry
        </p>
    </body>
    </html>
    """
    
    text_body = f"""
ClawSkills Verification Code

Your AI agent is requesting a ClawSkills API key.

Give this verification code to your agent:

    {verification_code}

Copy this code and paste it into your chat with your AI agent.
Your agent will handle the rest!

This code expires in 24 hours.
If you didn't request this, you can ignore this email.

ClawSkills - AI Agent Skill Registry
https://clawskills.tech
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
