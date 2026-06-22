import os
import csv
import io
import socket
import smtplib
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(to_email, subject, html):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not smtp_host:
        raise ValueError("SMTP_HOST missing")

    if not smtp_email or not smtp_password:
        raise ValueError("SMTP credentials missing")

    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = smtp_email
    msg["To"] = to_email

    server = smtplib.SMTP(smtp_host, smtp_port)
    server.set_debuglevel(1)  # 👈 IMPORTANT for debugging
    server.starttls()
    server.login(smtp_email, smtp_password)
    server.sendmail(smtp_email, [to_email], msg.as_string())
    server.quit()

    return True

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
        settings.sender_name  = request.form.get('sender_name', '').strip()
        settings.sender_email = request.form.get('sender_email', '').strip()
        settings.updated_at   = datetime.utcnow()
        db.session.commit()
        flash('Sender settings saved successfully.', 'success')
        return redirect(url_for('admin_email_settings'))

    smtp_configured = bool(
        os.environ.get('SMTP_EMAIL', '').strip() and
        os.environ.get('SMTP_PASSWORD', '').strip()
    )
    return render_template('admin_email_settings.html',
                           settings=settings,
                           smtp_configured=smtp_configured)


@app.route('/admin/test-smtp', methods=['POST'])
@login_required
def admin_test_smtp():
    """Send a live test email via Gmail SMTP to verify credentials."""
    settings = EmailSettings.query.first()
    cfg, err = _resolve_smtp_config(settings)
    if err:
        flash(f'Cannot send test — {err}', 'danger')
        return redirect(url_for('admin_email_settings'))

    test_html = """
    <html><body style="font-family:Arial,sans-serif;color:#1e3a5f;padding:20px;">
      <h2 style="color:#1a5276;">&#10003; Gmail SMTP test successful</h2>
      <p>Your HealthBridge email configuration is working correctly.</p>
    </body></html>
    """
    ok, send_err = _send_single_email(
        cfg, cfg['sender_email'], 'Admin',
        'HealthBridge – SMTP Test ✓', test_html,
    )
    if ok:
        flash(f'✓ Test email sent to {cfg["sender_email"]} via Gmail SMTP.', 'success')
    else:
        flash(f'Test email failed: {send_err}', 'danger')

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
            flash(f'Email service not configured: {cfg_err}', 'danger')
            return redirect(url_for('admin_send_newsletter'))

        if not subscribers:
            flash('There are no subscribers to send to.', 'warning')
            return redirect(url_for('admin_send_newsletter'))

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
#  EMAIL HELPERS  —  Gmail SMTP with App Password
# ════════════════════════════════════════════════════════════════════════════
#
# All Resend code has been removed.  Email delivery now uses Gmail SMTP
# via smtplib + STARTTLS on port 587.
#
# REQUIRED ENVIRONMENT VARIABLES
#   SMTP_EMAIL      your Gmail address, e.g. yourname@gmail.com
#   SMTP_PASSWORD   a 16-character Google App Password (NOT your Gmail password)
#
# OPTIONAL ENVIRONMENT VARIABLES (fall back to Admin → Email Settings DB values)
#   SMTP_SENDER_NAME   display name shown in From field  (default: HealthBridge NGO)
#
# HOW TO GENERATE A GMAIL APP PASSWORD
#   1. Enable 2-Step Verification at myaccount.google.com → Security
#   2. Search "App passwords" in Google Account
#   3. Create a new App Password for "Mail"
#   4. Copy the 16-character code (spaces optional — they are stripped)
#   5. Set it as SMTP_PASSWORD in your Render / Railway environment
#
# IPv4-FORCED CONNECT — WHY IT'S NEEDED
#   Gmail's DNS returns IPv6 addresses before IPv4.  On cloud containers
#   (Render, Railway, Docker) that have no IPv6 routing, the default
#   socket.create_connection() fails immediately with OSError Errno 97
#   (EAFNOSUPPORT) or Errno 101 (ENETUNREACH) before ever trying IPv4.
#   _smtp_connect() resolves only AF_INET records and builds the socket
#   manually, which guarantees the connection always goes over IPv4.

_SMTP_HOST    = 'smtp.gmail.com'
_SMTP_PORT    = 587
_SMTP_TIMEOUT = 20   # seconds — long enough for slow Render cold starts


def _resolve_smtp_config(settings=None):
    """
    Build the SMTP config dict from environment variables with DB as fallback
    for non-sensitive display fields.
    Returns (cfg_dict, None) on success or (None, error_str) when required
    credentials are missing.  Never raises.

    SMTP_EMAIL and SMTP_PASSWORD must come from environment variables.
    Sender display name falls back to the DB (Admin → Email Settings).
    """
    if settings is None:
        settings = EmailSettings.query.first()

    smtp_email    = os.environ.get('SMTP_EMAIL',    '').strip()
    smtp_password = os.environ.get('SMTP_PASSWORD', '').replace(' ', '')  # strip spaces from 16-char code

    sender_name = (
        os.environ.get('SMTP_SENDER_NAME', '').strip()
        or (settings and settings.sender_name or '')
        or 'HealthBridge NGO'
    )
    # Sender display address defaults to the authenticated Gmail account
    sender_email = (
        (settings and settings.sender_email or '')
        or smtp_email
    )

    if not smtp_email:
        return None, (
            'SMTP_EMAIL environment variable is not set. '
            'Add your Gmail address in your Render / Railway environment variables.'
        )
    if not smtp_password:
        return None, (
            'SMTP_PASSWORD environment variable is not set. '
            'Add your 16-character Gmail App Password in your Render / Railway '
            'environment variables. Generate one at: '
            'myaccount.google.com → Security → App passwords.'
        )

    logger.debug('SMTP config resolved — account=%s sender=%s', smtp_email, sender_email)
    return {
        'smtp_email':    smtp_email,
        'smtp_password': smtp_password,
        'sender_email':  sender_email,
        'sender_name':   sender_name,
    }, None


def _smtp_connect() -> smtplib.SMTP:
    """
    Open a raw SMTP connection to smtp.gmail.com:587 over IPv4 only.

    Forces AF_INET resolution to avoid Errno 97/101 on IPv6-less cloud
    containers where Gmail's DNS returns IPv6 records first.
    The returned object has read the 220 greeting banner; the caller must
    run ehlo / starttls / login / quit.
    """
    logger.debug('SMTP connect → %s:%d (IPv4-forced)', _SMTP_HOST, _SMTP_PORT)

    try:
        records = socket.getaddrinfo(
            _SMTP_HOST, _SMTP_PORT, socket.AF_INET, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise OSError(f'DNS lookup failed for {_SMTP_HOST}: {exc}') from exc

    if not records:
        raise OSError(f'No IPv4 address found for {_SMTP_HOST}')

    ipv4 = records[0][4][0]
    logger.debug('Resolved %s → %s (IPv4)', _SMTP_HOST, ipv4)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_SMTP_TIMEOUT)
    try:
        sock.connect((ipv4, _SMTP_PORT))
    except OSError:
        sock.close()
        raise

    # Attach the live socket to a bare SMTP instance (no auto-connect).
    # CRITICAL: set _host so starttls() passes the correct server_hostname
    # to ssl.wrap_socket().  Without it, ssl raises:
    #   ValueError: server_hostname cannot be an empty string or start with a dot.
    smtp = smtplib.SMTP(timeout=_SMTP_TIMEOUT)
    smtp._host = _SMTP_HOST   # required for TLS SNI
    smtp.sock  = sock
    smtp.file  = sock.makefile('rb')

    # Read the 220 greeting that the server sends on connect
    code, msg = smtp.getreply()
    if code != 220:
        smtp.close()
        raise smtplib.SMTPConnectError(code, msg)

    logger.debug('SMTP 220 banner: %s', msg[:80])
    return smtp


def _send_single_email(cfg, to_email, to_name, subject, body_html):
    """
    Send one HTML email via Gmail SMTP.
    Returns (True, None) on success or (False, human_readable_error) on any
    failure.  Never raises — safe to call in a loop from a Flask route.
    """
    logger.info('SMTP → %s  subject="%s"  from=%s',
                to_email, subject[:60], cfg['sender_email'])
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject']  = subject
        msg['From']     = f'{cfg["sender_name"]} <{cfg["sender_email"]}>'
        msg['To']       = f'{to_name} <{to_email}>'
        msg['Reply-To'] = cfg['sender_email']
        msg.attach(MIMEText(body_html, 'html'))

        server = _smtp_connect()
        try:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg['smtp_email'], cfg['smtp_password'])
            logger.debug('SMTP login OK — %s', cfg['smtp_email'])
            server.sendmail(cfg['sender_email'], to_email, msg.as_string())
            logger.info('SMTP delivered → %s', to_email)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        return True, None

    except smtplib.SMTPAuthenticationError as exc:
        err = (
            'Gmail rejected the login credentials. '
            'SMTP_PASSWORD must be a 16-character App Password, not your regular '
            'Gmail password. Generate one at: '
            'myaccount.google.com → Security → App passwords. '
            f'(SMTP {exc.smtp_code})'
        )
        logger.error('SMTP auth failed for %s: %s', cfg['smtp_email'], exc)
        return False, err

    except smtplib.SMTPRecipientsRefused as exc:
        err = f'Recipient address refused by Gmail: {to_email}. ({exc})'
        logger.warning('SMTP recipient refused: %s', to_email)
        return False, err

    except smtplib.SMTPServerDisconnected as exc:
        err = f'Gmail disconnected unexpectedly: {exc}'
        logger.error('SMTP disconnected sending to %s: %s', to_email, exc)
        return False, err

    except smtplib.SMTPConnectError as exc:
        err = f'Cannot connect to {_SMTP_HOST}:{_SMTP_PORT}: {exc}'
        logger.error('SMTPConnectError: %s', exc)
        return False, err

    except smtplib.SMTPException as exc:
        err = f'SMTP protocol error sending to {to_email}: {exc}'
        logger.error('SMTPException to %s: %s', to_email, exc)
        return False, err

    except (socket.timeout, TimeoutError):
        err = (
            f'Connection to {_SMTP_HOST}:{_SMTP_PORT} timed out after {_SMTP_TIMEOUT}s. '
            'Ensure outbound TCP port 587 is not blocked in your hosting environment.'
        )
        logger.error('SMTP timeout sending to %s', to_email)
        return False, err

    except OSError as exc:
        err = (
            f'Network error connecting to {_SMTP_HOST}:{_SMTP_PORT}: {exc}. '
            'Ensure outbound TCP port 587 is open in your hosting environment.'
        )
        logger.error('SMTP OSError to %s — errno=%s: %s', to_email, exc.errno, exc)
        return False, err

    except Exception as exc:
        err = f'Unexpected error sending to {to_email}: {type(exc).__name__}: {exc}'
        logger.exception('Unexpected SMTP error to %s', to_email)
        return False, err


def _send_welcome_email(to_email, to_name):
    """Fire-and-forget welcome email.  Logs on failure, never raises."""
    cfg, err = _resolve_smtp_config()
    if err:
        logger.info('Welcome email skipped for %s (SMTP not configured): %s',
                    to_email, err)
        return

    sender_name = cfg['sender_name']
    subject     = f'Welcome to {sender_name} Newsletter!'
    body_html   = f"""\
<html>
<body style="font-family:Arial,sans-serif;color:#1e3a5f;max-width:600px;
             margin:0 auto;padding:24px;">
  <div style="background:linear-gradient(135deg,#1a5276,#2471a3);
              padding:28px 32px;border-radius:10px 10px 0 0;">
    <h1 style="color:#fff;font-size:22px;margin:0;">Welcome to HealthBridge!</h1>
  </div>
  <div style="background:#f0f7fd;padding:28px 32px;
              border-radius:0 0 10px 10px;border:1px solid #d6eaf8;">
    <p style="font-size:16px;">Hi <strong>{to_name}</strong>,</p>
    <p>Thank you for subscribing. You will receive updates on our health
       outreach programs, community events, and volunteer opportunities.</p>
    <p>Together we can make a real difference.</p>
    <br>
    <p style="color:#5d7fa0;font-size:13px;">— {sender_name}</p>
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
