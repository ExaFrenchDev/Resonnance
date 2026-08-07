import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

from config import Config
from modules import database

_BASE = """<!doctype html>
<html lang="fr"><body style="margin:0;padding:32px 16px;background:#E6E4DC;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="520" cellpadding="0" cellspacing="0" style="max-width:520px;background:#F4F3EE;border:1.5px solid #16161A;border-radius:4px;overflow:hidden;">
<tr><td style="padding:28px 32px 8px;">
<div style="font-size:11px;letter-spacing:.32em;text-transform:uppercase;color:#FF4A1C;font-weight:700;">Resonance</div>
</td></tr>
<tr><td style="padding:8px 32px 32px;color:#16161A;font-size:15px;line-height:1.65;">
{content}
</td></tr>
<tr><td style="padding:18px 32px;background:#E6E4DC;border-top:1.5px solid #16161A;color:#8B8991;font-size:12px;line-height:1.6;">
Tu reçois cet email parce que tu as un compte Resonance.<br>
<a href="{unsub}" style="color:#2438C8;">Gérer mes emails</a>
</td></tr>
</table></td></tr></table></body></html>"""


def _render(content, base_url=""):
    return _BASE.format(content=content, unsub=f"{base_url}/parametres")


def _send_sync(to_address, subject, html, text):
    if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
        print(f"[mail:console] -> {to_address} | {subject}\n{text}\n")
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((Config.MAIL_FROM_NAME, Config.MAIL_FROM_ADDRESS))
    message["To"] = to_address
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    try:
        if Config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, context=ssl.create_default_context(), timeout=20) as server:
                server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=20) as server:
                if Config.SMTP_USE_TLS:
                    server.starttls(context=ssl.create_default_context())
                server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                server.send_message(message)
        return True
    except Exception as error:
        print(f"[mail:error] {to_address} -> {error}")
        return False


def send(to_address, subject, html, text, blocking=False):
    if blocking:
        return _send_sync(to_address, subject, html, text)
    thread = threading.Thread(target=_send_sync, args=(to_address, subject, html, text), daemon=True)
    thread.start()
    return True


def send_verification_code(to_address, display_name, code, base_url=""):
    content = f"""
<h1 style="font-size:26px;margin:12px 0 6px;color:#16161A;font-weight:800;">Salut {display_name},</h1>
<p style="margin:0 0 22px;color:#56555E;">Voici ton code de confirmation. Il expire dans {Config.CODE_TTL_MINUTES} minutes.</p>
<div style="font-family:monospace;font-size:34px;letter-spacing:.36em;color:#16161A;background:#E6E4DC;border:1.5px solid #16161A;border-radius:4px;padding:20px;text-align:center;font-weight:700;">{code}</div>
<p style="margin:22px 0 0;color:#8B8991;font-size:13px;">Si tu n'es pas à l'origine de cette inscription, ignore ce message.</p>"""
    text = f"Ton code de confirmation Resonance : {code} (valable {Config.CODE_TTL_MINUTES} minutes)."
    return send(to_address, f"{code} — ton code de confirmation Resonance", _render(content, base_url), text)


def send_match_alert(to_address, display_name, other_name, score, base_url=""):
    content = f"""
<h1 style="font-size:26px;margin:12px 0 6px;color:#16161A;font-weight:800;">{score}% avec {other_name}</h1>
<p style="margin:0 0 22px;color:#56555E;">{display_name}, vos deux profils entrent en résonance. La discussion est ouverte.</p>
<a href="{base_url}/decouvrir" style="display:inline-block;background:#FF4A1C;color:#F4F3EE;text-decoration:none;font-weight:700;padding:14px 26px;border-radius:4px;">Voir le profil</a>"""
    text = f"{display_name}, tu matches à {score}% avec {other_name}. Rendez-vous sur Resonance."
    return send(to_address, f"{score}% de résonance avec {other_name}", _render(content, base_url), text)


def send_new_message_alert(to_address, display_name, other_name, base_url=""):
    content = f"""
<h1 style="font-size:24px;margin:12px 0 6px;color:#16161A;font-weight:800;">{other_name} t'a écrit</h1>
<p style="margin:0 0 22px;color:#56555E;">{display_name}, un message t'attend dans ta messagerie.</p>
<a href="{base_url}/messages" style="display:inline-block;background:#2438C8;color:#FFFFFF;text-decoration:none;font-weight:700;padding:14px 26px;border-radius:4px;">Lire le message</a>"""
    text = f"{other_name} t'a envoyé un message sur Resonance."
    return send(to_address, f"Nouveau message de {other_name}", _render(content, base_url), text)


def broadcast_announcement(title, body, base_url=""):
    recipients = database.query_all(
        "SELECT email, COALESCE(display_name, username) AS name FROM users WHERE is_verified = 1 AND newsletter = 1"
    )
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;color:#56555E;">{line.strip()}</p>'
        for line in body.split("\n")
        if line.strip()
    )
    announcement_id = database.execute(
        "INSERT INTO announcements (title, body, sent_to) VALUES (?, ?, ?)",
        (title, body, len(recipients)),
    )

    def worker():
        for person in recipients:
            content = f"""
<h1 style="font-size:26px;margin:12px 0 14px;color:#16161A;font-weight:800;">{title}</h1>
{paragraphs}
<a href="{base_url}/decouvrir" style="display:inline-block;margin-top:14px;background:#16161A;color:#F4F3EE;text-decoration:none;font-weight:700;padding:14px 26px;border-radius:4px;">Ouvrir Resonance</a>"""
            _send_sync(person["email"], title, _render(content, base_url), f"{title}\n\n{body}")

    threading.Thread(target=worker, daemon=True).start()
    return announcement_id, len(recipients)
