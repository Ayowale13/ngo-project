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

        conn_ok, conn_err = _validate_smtp_connection(cfg)
        if not conn_ok:
            flash(f'SMTP connection failed — no emails sent. {conn_err}', 'danger')
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
#  EMAIL HELPERS
# ════════════════════════════════════════════════════════════════════════════
# Credential priority: env vars (Render / .env) > DB (Admin → Email Settings)

_SMTP_ERRORS = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPException,
    socket.timeout,
    OSError,
)
_SMTP_TIMEOUT = 15  # seconds — keeps Gunicorn workers from hanging


def _resolve_smtp_config(settings=None):
    if settings is None:
        settings = EmailSettings.query.first()

    sender_email  = os.environ.get('SMTP_SENDER_EMAIL') or (settings and settings.sender_email)  or ''
    sender_name   = os.environ.get('SMTP_SENDER_NAME')  or (settings and settings.sender_name)   or 'HealthBridge NGO'
    smtp_host     = os.environ.get('SMTP_HOST')         or (settings and settings.smtp_host)      or ''
    smtp_password = os.environ.get('SMTP_PASSWORD')     or (settings and settings.get_smtp_password()) or ''
    smtp_port_raw = os.environ.get('SMTP_PORT')         or (settings and settings.smtp_port)      or 587

    try:
        smtp_port = int(smtp_port_raw)
    except (TypeError, ValueError):
        smtp_port = 587

    if not sender_email:
        return None, 'Sender email not configured. Set it in Admin → Email Settings or SMTP_SENDER_EMAIL env var.'
    if not smtp_host:
        return None, 'SMTP host not configured. Set it in Admin → Email Settings or SMTP_HOST env var.'
    if not smtp_password:
        return None, 'SMTP password not configured. Set it in Admin → Email Settings or SMTP_PASSWORD env var.'

    return {
        'sender_email':  sender_email,
        'sender_name':   sender_name,
        'smtp_host':     smtp_host,
        'smtp_port':     smtp_port,
        'smtp_password': smtp_password,
    }, None


def _validate_smtp_connection(cfg):
    try:
        with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=_SMTP_TIMEOUT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg['sender_email'], cfg['smtp_password'])
        logger.info('SMTP test connection succeeded for %s', cfg['sender_email'])
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, ('Gmail rejected the login. Use a 16-character App Password, '
                       'not your regular Gmail password.')
    except smtplib.SMTPConnectError as e:
        return False, f'Could not connect to {cfg["smtp_host"]}:{cfg["smtp_port"]}. ({e})'
    except socket.timeout:
        return False, f'Connection timed out after {_SMTP_TIMEOUT}s.'
    except OSError as e:
        return False, f'Network error: {e}'
    except _SMTP_ERRORS as e:
        return False, f'SMTP error: {e}'


def _send_single_email(cfg, to_email, to_name, subject, body_html):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject']  = subject
        msg['From']     = f'{cfg["sender_name"]} <{cfg["sender_email"]}>'
        msg['To']       = f'{to_name} <{to_email}>'
        msg['Reply-To'] = cfg['sender_email']
        msg.attach(MIMEText(body_html, 'html'))

        with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=_SMTP_TIMEOUT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg['sender_email'], cfg['smtp_password'])
            server.sendmail(cfg['sender_email'], to_email, msg.as_string())

        logger.info('Email sent → %s', to_email)
        return True, None

    except smtplib.SMTPAuthenticationError:
        err = 'SMTP authentication failed. Check App Password.'
        logger.error(err)
        return False, err
    except smtplib.SMTPRecipientsRefused:
        err = f'Recipient refused: {to_email}'
        logger.warning(err)
        return False, err
    except smtplib.SMTPServerDisconnected as e:
        err = f'Server disconnected: {e}'
        logger.error(err)
        return False, err
    except socket.timeout:
        err = f'Timed out after {_SMTP_TIMEOUT}s.'
        logger.error(err)
        return False, err
    except OSError as e:
        err = f'Network error: {e}'
        logger.error(err)
        return False, err
    except smtplib.SMTPException as e:
        err = f'SMTP error: {e}'
        logger.error(err)
        return False, err


def _send_welcome_email(to_email, to_name):
    cfg, err = _resolve_smtp_config()
    if err:
        logger.info('Welcome email skipped (%s): %s', to_email, err)
        return
    subject = f'Welcome to {cfg["sender_name"]} Newsletter!'
    body_html = f"""
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
    </html>
    """
    ok, send_err = _send_single_email(cfg, to_email, to_name, subject, body_html)
    if not ok:
        logger.warning('Welcome email failed for %s: %s', to_email, send_err)


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

init_db()

if __name__ == '__main__':
    app.run(debug=True)
