import os
import csv
import io
import socket
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Load .env when present (local dev). No-op if file is missing.
from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, render_template, redirect, url_for,
                   flash, request, Response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)

from config import Config
from models import db, AdminUser, Subscriber, Volunteer, EmailSettings

# ── Logging (visible in Gunicorn output on Render) ───────────────────────────
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')

# ── App + extensions ─────────────────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'
login_manager.login_message = 'Please log in to access the admin dashboard.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return AdminUser.query.get(int(user_id))


# ── Database initialisation ──────────────────────────────────────────────────

def init_db():
    os.makedirs('instance', exist_ok=True)
    with app.app_context():
        db.create_all()
        if not AdminUser.query.first():
            admin = AdminUser(username='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print('Default admin created  →  username: admin  |  password: admin123')
        if not EmailSettings.query.first():
            db.session.add(EmailSettings(
                sender_name='NGO Outreach Team',
                smtp_host='smtp.gmail.com',
                smtp_port=587,
                mail_service='smtp',
            ))
            db.session.commit()


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/programs')
def programs():
    return render_template('programs.html')


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        flash('Thank you for reaching out! We will get back to you shortly.', 'success')
        return redirect(url_for('contact'))
    return render_template('contact.html')


@app.route('/volunteer', methods=['GET', 'POST'])
def volunteer():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email     = request.form.get('email', '').strip()
        phone     = request.form.get('phone', '').strip()
        skills    = request.form.get('skills', '').strip()
        message   = request.form.get('message', '').strip()

        if not full_name or not email or not phone:
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('volunteer'))

        db.session.add(Volunteer(
            full_name=full_name, email=email, phone=phone,
            skills=skills, message=message,
        ))
        db.session.commit()
        flash('Thank you for signing up to volunteer! We will contact you soon.', 'success')
        return redirect(url_for('volunteer'))

    return render_template('volunteer.html')


@app.route('/subscribe', methods=['POST'])
def subscribe():
    full_name = request.form.get('full_name', '').strip()
    email     = request.form.get('email', '').strip()
    phone     = request.form.get('phone', '').strip()

    if not full_name or not email:
        flash('Name and email are required for subscription.', 'danger')
        return redirect(request.referrer or url_for('index'))

    if Subscriber.query.filter_by(email=email).first():
        flash('This email is already subscribed to our newsletter.', 'info')
        return redirect(request.referrer or url_for('index'))

    db.session.add(Subscriber(full_name=full_name, email=email, phone=phone or None))
    db.session.commit()
    _send_welcome_email(email, full_name)
    flash('You have successfully subscribed to our newsletter!', 'success')
    return redirect(request.referrer or url_for('index'))


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — AUTHENTICATION
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        admin    = AdminUser.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            login_user(admin, remember=False)
            flash('Welcome back!', 'success')
            return redirect(url_for('admin_dashboard'))

        flash('Invalid username or password.', 'danger')

    return render_template('admin_login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — CHANGE PASSWORD
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/change-password', methods=['GET', 'POST'])
@login_required
def admin_change_password():
    """Allow the currently logged-in admin to update their password."""
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw     = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        # 1 — current password must be correct
        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'danger')
            return redirect(url_for('admin_change_password'))

        # 2 — new password minimum length
        if len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
            return redirect(url_for('admin_change_password'))

        # 3 — confirmation must match
        if new_pw != confirm_pw:
            flash('New password and confirmation do not match.', 'danger')
            return redirect(url_for('admin_change_password'))

        # 4 — must actually be different
        if new_pw == current_pw:
            flash('New password must be different from your current password.', 'danger')
            return redirect(url_for('admin_change_password'))

        # All checks passed — hash and persist
        current_user.set_password(new_pw)
        db.session.commit()
        logger.info('Admin "%s" changed their password successfully.', current_user.username)
        flash('Password updated successfully. Use your new password next time you log in.', 'success')
        return redirect(url_for('admin_change_password'))

    return render_template('admin_change_password.html')


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — DASHBOARD
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    subscribers = Subscriber.query.order_by(Subscriber.date_subscribed.desc()).all()
    volunteers  = Volunteer.query.order_by(Volunteer.date_submitted.desc()).all()
    now         = datetime.utcnow()
    stats = {
        'total_subscribers':    Subscriber.query.count(),
        'total_volunteers':     Volunteer.query.count(),
        'new_this_month':       Subscriber.query.filter(
            db.extract('month', Subscriber.date_subscribed) == now.month,
            db.extract('year',  Subscriber.date_subscribed) == now.year,
        ).count(),
        'volunteers_this_month': Volunteer.query.filter(
            db.extract('month', Volunteer.date_submitted) == now.month,
            db.extract('year',  Volunteer.date_submitted) == now.year,
        ).count(),
    }
    return render_template('admin_dashboard.html',
                           subscribers=subscribers,
                           volunteers=volunteers,
                           stats=stats)


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — RECORDS
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/delete/subscriber/<int:sub_id>', methods=['POST'])
@login_required
def delete_subscriber(sub_id):
    sub = Subscriber.query.get_or_404(sub_id)
    db.session.delete(sub)
    db.session.commit()
    flash('Subscriber deleted.', 'success')
    return redirect(url_for('admin_dashboard') + '#subscribers')


@app.route('/admin/delete/volunteer/<int:vol_id>', methods=['POST'])
@login_required
def delete_volunteer(vol_id):
    vol = Volunteer.query.get_or_404(vol_id)
    db.session.delete(vol)
    db.session.commit()
    flash('Volunteer record deleted.', 'success')
    return redirect(url_for('admin_dashboard') + '#volunteers')


@app.route('/admin/export/subscribers')
@login_required
def export_subscribers():
    subscribers = Subscriber.query.order_by(Subscriber.date_subscribed.desc()).all()
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(['ID', 'Full Name', 'Email', 'Phone', 'Date Subscribed'])
    for s in subscribers:
        w.writerow([s.id, s.full_name, s.email, s.phone or '',
                    s.date_subscribed.strftime('%Y-%m-%d %H:%M')])
    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=subscribers.csv'})


@app.route('/admin/export/volunteers')
@login_required
def export_volunteers():
    volunteers = Volunteer.query.order_by(Volunteer.date_submitted.desc()).all()
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(['ID', 'Full Name', 'Email', 'Phone', 'Skills', 'Message', 'Date Submitted'])
    for v in volunteers:
        w.writerow([v.id, v.full_name, v.email, v.phone,
                    v.skills or '', v.message or '',
                    v.date_submitted.strftime('%Y-%m-%d %H:%M')])
    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=volunteers.csv'})


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — EMAIL SETTINGS
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/email-settings', methods=['GET', 'POST'])
@login_required
def admin_email_settings():
    settings = EmailSettings.query.first()
    if not settings:
        settings = EmailSettings()
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.sender_email = request.form.get('sender_email', '').strip()
        settings.sender_name  = request.form.get('sender_name', '').strip()
        settings.smtp_host    = request.form.get('smtp_host', '').strip()
        port_raw = request.form.get('smtp_port', '587').strip()
        settings.smtp_port    = int(port_raw) if port_raw.isdigit() else 587
        settings.mail_service = request.form.get('mail_service', 'smtp')

        smtp_pw = request.form.get('smtp_password', '').strip()
        if smtp_pw:
            settings.set_smtp_password(smtp_pw)

        api_key = request.form.get('api_key', '').strip()
        if api_key:
            settings.set_api_key(api_key)

        settings.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Email settings saved successfully.', 'success')
        return redirect(url_for('admin_email_settings'))

    return render_template('admin_email_settings.html', settings=settings)


@app.route('/admin/test-smtp', methods=['POST'])
@login_required
def admin_test_smtp():
    settings = EmailSettings.query.first()
    cfg, err = _resolve_smtp_config(settings)
    if err:
        flash(f'Cannot test — configuration incomplete: {err}', 'danger')
        return redirect(url_for('admin_email_settings'))

    conn_ok, conn_err = _validate_smtp_connection(cfg)
    if not conn_ok:
        flash(f'SMTP connection failed: {conn_err}', 'danger')
        return redirect(url_for('admin_email_settings'))

    test_html = """
    <html><body style="font-family:Arial,sans-serif;color:#1e3a5f;padding:20px;">
      <h2 style="color:#1a5276;">&#10003; SMTP test successful</h2>
      <p>Your HealthBridge email settings are working correctly.</p>
    </body></html>
    """
    ok, send_err = _send_single_email(
        cfg, cfg['sender_email'], 'Admin',
        'HealthBridge – SMTP Test ✓', test_html,
    )
    if ok:
        flash(f'SMTP test passed ✓  A confirmation email was sent to {cfg["sender_email"]}.', 'success')
    else:
        flash(f'Connection OK but sending failed: {send_err}', 'warning')

    return redirect(url_for('admin_email_settings'))


# ════════════════════════════════════════════════════════════════════════════
#  ADMIN — SEND NEWSLETTER
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/send-newsletter', methods=['GET', 'POST'])
@login_required
def admin_send_newsletter():
    settings    = EmailSettings.query.first()
    subscribers = Subscriber.query.all()

    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()

        if not subject or not body:
            flash('Subject and body are required.', 'danger')
            return redirect(url_for('admin_send_newsletter'))

        cfg, cfg_err = _resolve_smtp_config(settings)
        if cfg_err:
            flash(f'Cannot send – email not configured: {cfg_err}', 'danger')
            return redirect(url_for('admin_send_newsletter'))

        if not subscribers:
            flash('There are no subscribers to send to.', 'warning')
            return redirect(url_for('admin_send_newsletter'))

        # No separate pre-flight connection — _send_single_email handles every
        # error class (auth, network, TLS, refused) and returns (False, msg).
        # A redundant validate call would open+close a connection for nothing
        # and double the failure surface before any email is sent.
        sent, failed, errors = 0, 0, []
        for sub in subscribers:
            ok, err = _send_single_email(cfg, sub.email, sub.full_name, subject, body)
            if ok:
                sent += 1
            else:
                failed += 1
                errors.append(f'{sub.email}: {err}')

        if failed == 0:
            flash(f'✓ Newsletter sent to all {sent} subscriber(s).', 'success')
        elif sent == 0:
            flash(f'✗ All {failed} emails failed. First error: {errors[0]}', 'danger')
        else:
            flash(f'Partial: {sent} sent, {failed} failed. First failure: {errors[0]}', 'warning')

        logger.info('Newsletter send — sent=%d failed=%d subject="%s"', sent, failed, subject)
        return redirect(url_for('admin_send_newsletter'))

    cfg, cfg_err = _resolve_smtp_config(settings)
    return render_template('admin_send_newsletter.html',
                           settings=settings,
                           count=len(subscribers),
                           smtp_ready=(cfg_err is None),
                           smtp_error=cfg_err)


# ════════════════════════════════════════════════════════════════════════════
#  EMAIL HELPERS
# ════════════════════════════════════════════════════════════════════════════
# Credential priority: env vars (Render / .env) > DB (Admin → Email Settings)
#
# ROOT-CAUSE NOTE — "Network is unreachable" / Errno 97 / Errno 101
# ──────────────────────────────────────────────────────────────────
# smtplib.SMTP(host, port) calls socket.create_connection() internally.
# create_connection() iterates through ALL getaddrinfo() results in order.
# Gmail's DNS returns IPv6 addresses *before* IPv4.  On hosts with no IPv6
# routing (many cloud sandboxes, Render free tier, Docker containers) the
# very first connect attempt raises OSError(97, 'Address family not
# supported') or OSError(101, 'Network is unreachable') and Python does NOT
# fall through to the next address — it raises immediately.
#
# Fix: _smtp_connect() resolves only AF_INET (IPv4) addresses first and
# builds a manual socket before handing it to smtplib.  This guarantees we
# never attempt an IPv6 connection on an IPv4-only host.

import errno as _errno_mod

_SMTP_TIMEOUT = 15          # seconds — prevents Gunicorn workers from hanging
_NETWORK_ERRNOS = frozenset({
    _errno_mod.ENETUNREACH,   # 101  Network is unreachable
    _errno_mod.EAFNOSUPPORT,  # 97   Address family not supported by protocol
    _errno_mod.ECONNREFUSED,  # 111  Connection refused
    _errno_mod.EHOSTUNREACH,  # 113  No route to host
})


def _smtp_connect(host: str, port: int) -> smtplib.SMTP:
    """
    Return a fresh smtplib.SMTP instance whose underlying socket was created
    explicitly over IPv4 (AF_INET).  This sidesteps the IPv6-first ordering
    that getaddrinfo() returns on dual-stack hosts where IPv6 is not routed,
    which would otherwise raise Errno 97/101 before Python ever tries IPv4.
    """
    logger.debug('SMTP connect → %s:%d (IPv4-forced)', host, port)

    # Resolve only IPv4 addresses
    try:
        records = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise OSError(f'DNS resolution failed for {host}: {e}') from e

    if not records:
        raise OSError(f'No IPv4 address found for {host}')

    ipv4_addr = records[0][4][0]
    logger.debug('Resolved %s → %s (IPv4)', host, ipv4_addr)

    # Build an explicit IPv4 socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_SMTP_TIMEOUT)
    try:
        sock.connect((ipv4_addr, port))
    except OSError:
        sock.close()
        raise

    # Attach the pre-connected socket to a bare SMTP instance.
    # smtplib.SMTP.__init__ with no host arg skips its internal connect, which
    # is what we want — but it also leaves self._host = '' (empty string).
    # starttls() later calls:
    #     context.wrap_socket(self.sock, server_hostname=self._host)
    # ssl raises ValueError if server_hostname is '' or starts with '.'.
    # Fix: set _host explicitly before any TLS call.
    smtp = smtplib.SMTP(timeout=_SMTP_TIMEOUT)
    smtp._host = host          # ← must be set for starttls() SSL SNI to work
    smtp.sock  = sock
    smtp.file  = smtp.sock.makefile('rb')

    # Read the server greeting banner (220) — replaces what __init__ would do
    code, msg = smtp.getreply()
    if code != 220:
        smtp.close()
        raise smtplib.SMTPConnectError(code, msg)

    logger.debug('SMTP banner: %d %s', code, msg[:60])
    return smtp


def _resolve_smtp_config(settings=None):
    """
    Build the SMTP config dict from env vars (highest priority) with DB as
    fallback.  Returns (cfg_dict, None) on success or (None, error_str) on
    missing fields — never raises.
    """
    if settings is None:
        settings = EmailSettings.query.first()

    sender_email  = os.environ.get('SMTP_SENDER_EMAIL') or (settings and settings.sender_email)        or ''
    sender_name   = os.environ.get('SMTP_SENDER_NAME')  or (settings and settings.sender_name)         or 'HealthBridge NGO'
    smtp_host     = os.environ.get('SMTP_HOST')         or (settings and settings.smtp_host)           or ''
    smtp_password = os.environ.get('SMTP_PASSWORD')     or (settings and settings.get_smtp_password()) or ''
    smtp_port_raw = os.environ.get('SMTP_PORT')         or (settings and settings.smtp_port)           or 587

    try:
        smtp_port = int(smtp_port_raw)
    except (TypeError, ValueError):
        smtp_port = 587

    # Detailed missing-field errors help the admin fix the issue directly
    if not sender_email:
        return None, ('Sender email not set. '
                      'Go to Admin → Email Settings or set SMTP_SENDER_EMAIL env var.')
    if not smtp_host:
        return None, ('SMTP host not set. '
                      'Go to Admin → Email Settings or set SMTP_HOST env var.')
    if not smtp_password:
        return None, ('SMTP password not set. '
                      'Go to Admin → Email Settings or set SMTP_PASSWORD env var.')

    logger.debug(
        'SMTP config resolved — from=%s host=%s port=%d (source: %s)',
        sender_email, smtp_host, smtp_port,
        'env' if os.environ.get('SMTP_SENDER_EMAIL') else 'db',
    )
    return {
        'sender_email':  sender_email,
        'sender_name':   sender_name,
        'smtp_host':     smtp_host,
        'smtp_port':     smtp_port,
        'smtp_password': smtp_password,
    }, None


def _friendly_network_error(exc, host, port):
    """Convert low-level OS network errors into admin-readable messages."""
    if isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS:
        return (
            f'Cannot reach {host}:{port} — outbound SMTP is blocked. '
            f'On Render, outbound port 587 must be open. '
            f'Try port 465 (SSL) if 587 is firewalled. '
            f'OS error: [{exc.errno}] {exc.strerror}'
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f'Connection to {host}:{port} timed out after {_SMTP_TIMEOUT}s. Check host and port.'
    return f'Network error connecting to {host}:{port}: {exc}'


def _validate_smtp_connection(cfg):
    """
    Open a real SMTP connection, run STARTTLS and login, then close cleanly.
    Returns (True, None) on success or (False, human_readable_error) on any
    failure.  Never raises — safe to call from a Flask route.
    """
    host, port = cfg['smtp_host'], cfg['smtp_port']
    logger.info('SMTP pre-flight check — host=%s port=%d user=%s', host, port, cfg['sender_email'])

    try:
        server = _smtp_connect(host, port)
        try:
            server.ehlo()
            server.starttls()
            server.ehlo()
            logger.debug('STARTTLS negotiated')
            server.login(cfg['sender_email'], cfg['smtp_password'])
            logger.info('SMTP login OK — %s', cfg['sender_email'])
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return True, None

    except smtplib.SMTPAuthenticationError as e:
        msg = (
            'Gmail rejected the login credentials. '
            'You must use a 16-character App Password (not your regular Gmail password). '
            'Generate one at myaccount.google.com → Security → App passwords. '
            f'(SMTP code {e.smtp_code})'
        )
        logger.error('SMTP auth failed for %s: %s', cfg['sender_email'], e)
        return False, msg

    except smtplib.SMTPConnectError as e:
        msg = f'SMTP server refused connection to {host}:{port}. ({e})'
        logger.error(msg)
        return False, msg

    except smtplib.SMTPException as e:
        msg = f'SMTP protocol error during handshake: {e}'
        logger.error(msg)
        return False, msg

    except OSError as e:
        msg = _friendly_network_error(e, host, port)
        logger.error('SMTP connect OSError for %s:%d — %s', host, port, e)
        return False, msg

    except Exception as e:
        msg = f'Unexpected error during SMTP test: {type(e).__name__}: {e}'
        logger.exception(msg)
        return False, msg


def _send_single_email(cfg, to_email, to_name, subject, body_html):
    """
    Send one HTML email.  Returns (True, None) on success or (False, err_str)
    on failure.  Never raises — safe to call in a loop from a Flask route.
    """
    host, port = cfg['smtp_host'], cfg['smtp_port']
    logger.info('Sending email → %s  subject="%s"  host=%s:%d',
                to_email, subject[:50], host, port)
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject']  = subject
        msg['From']     = f'{cfg["sender_name"]} <{cfg["sender_email"]}>'
        msg['To']       = f'{to_name} <{to_email}>'
        msg['Reply-To'] = cfg['sender_email']
        msg.attach(MIMEText(body_html, 'html'))

        server = _smtp_connect(host, port)
        try:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg['sender_email'], cfg['smtp_password'])
            logger.debug('SMTP login OK, sending to %s', to_email)
            server.sendmail(cfg['sender_email'], to_email, msg.as_string())
            logger.info('Email delivered → %s', to_email)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        return True, None

    except smtplib.SMTPAuthenticationError as e:
        err = (
            'Gmail rejected the App Password. '
            'Regenerate it at myaccount.google.com → Security → App passwords. '
            f'(SMTP {e.smtp_code})'
        )
        logger.error('Auth error sending to %s: %s', to_email, e)
        return False, err

    except smtplib.SMTPRecipientsRefused as e:
        err = f'Recipient address refused by server: {to_email}. ({e})'
        logger.warning(err)
        return False, err

    except smtplib.SMTPServerDisconnected as e:
        err = f'Server disconnected unexpectedly: {e}'
        logger.error('Disconnected sending to %s: %s', to_email, e)
        return False, err

    except smtplib.SMTPException as e:
        err = f'SMTP protocol error: {e}'
        logger.error('SMTPException sending to %s: %s', to_email, e)
        return False, err

    except OSError as e:
        err = _friendly_network_error(e, host, port)
        logger.error('OSError sending to %s — errno=%s: %s', to_email, e.errno, e)
        return False, err

    except Exception as e:
        err = f'Unexpected error sending to {to_email}: {type(e).__name__}: {e}'
        logger.exception(err)
        return False, err


def _send_welcome_email(to_email, to_name):
    """Fire-and-forget welcome email; logs but never raises or crashes the caller."""
    cfg, err = _resolve_smtp_config()
    if err:
        logger.info('Welcome email skipped for %s (no SMTP config): %s', to_email, err)
        return

    subject = f'Welcome to {cfg["sender_name"]} Newsletter!'
    body_html = f"""\
<html>
<body style="font-family:Arial,sans-serif;color:#1e3a5f;max-width:600px;margin:0 auto;padding:24px;">
  <div style="background:linear-gradient(135deg,#1a5276,#2471a3);padding:28px 32px;border-radius:10px 10px 0 0;">
    <h1 style="color:#ffffff;font-size:22px;margin:0;">Welcome to HealthBridge!</h1>
  </div>
  <div style="background:#f0f7fd;padding:28px 32px;border-radius:0 0 10px 10px;border:1px solid #d6eaf8;">
    <p style="font-size:16px;">Hi <strong>{to_name}</strong>,</p>
    <p>Thank you for subscribing. You will receive updates on our health outreach programs,
       community events, and volunteer opportunities.</p>
    <p>Together we can make a real difference.</p>
    <br>
    <p style="color:#5d7fa0;font-size:13px;">— {cfg["sender_name"]}</p>
  </div>
</body>
</html>"""

    ok, send_err = _send_single_email(cfg, to_email, to_name, subject, body_html)
    if not ok:
        logger.warning('Welcome email failed for %s: %s', to_email, send_err)


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

init_db()

if __name__ == '__main__':
    app.run(debug=True)
