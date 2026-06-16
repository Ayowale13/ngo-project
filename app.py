import os
import csv
import io
import logging
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
        # Resend-only: store sender identity only.
        # API key comes exclusively from RESEND_API_KEY env var — never stored in DB.
        settings.sender_name  = request.form.get('sender_name', '').strip()
        settings.sender_email = request.form.get('sender_email', '').strip()
        settings.updated_at   = datetime.utcnow()
        db.session.commit()
        flash('Sender settings saved successfully.', 'success')
        return redirect(url_for('admin_email_settings'))

    resend_key_set = bool(os.environ.get('RESEND_API_KEY', ''))
    return render_template('admin_email_settings.html',
                           settings=settings,
                           resend_key_set=resend_key_set)


@app.route('/admin/test-smtp', methods=['POST'])   # URL kept for any saved bookmarks
@login_required
def admin_test_smtp():
    """Send a live test email via Resend to confirm the API key and sender work."""
    settings = EmailSettings.query.first()
    cfg, err = _resolve_resend_config(settings)
    if err:
        flash(f'Cannot send test — {err}', 'danger')
        return redirect(url_for('admin_email_settings'))

    test_html = """
    <html><body style="font-family:Arial,sans-serif;color:#1e3a5f;padding:20px;">
      <h2 style="color:#1a5276;">&#10003; Resend test successful</h2>
      <p>Your HealthBridge Resend integration is working correctly.</p>
    </body></html>
    """
    ok, send_err = _send_single_email(
        cfg, cfg['sender_email'], 'Admin',
        'HealthBridge – Resend Test ✓', test_html,
    )
    if ok:
        flash(f'✓ Test email sent to {cfg["sender_email"]} via Resend.', 'success')
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

        cfg, cfg_err = _resolve_resend_config(settings)
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

    cfg, cfg_err = _resolve_resend_config(settings)
    return render_template('admin_send_newsletter.html',
                           settings=settings,
                           count=len(subscribers),
                           resend_ready=(cfg_err is None),
                           resend_error=cfg_err)


# ════════════════════════════════════════════════════════════════════════════
#  EMAIL HELPERS  —  Resend API (sole email provider)
# ════════════════════════════════════════════════════════════════════════════
# Gmail SMTP is completely removed.  All email delivery uses the Resend HTTP
# API (port 443 / HTTPS), which is never blocked by cloud platform firewalls.
#
# CONFIGURATION — two env vars required, one optional:
#   RESEND_API_KEY       re_xxxxxxxxxxxxxxxxxxxxxxxxxxxx   ← required, env only
#   RESEND_SENDER_EMAIL  hello@yourdomain.com              ← required
#   RESEND_SENDER_NAME   HealthBridge NGO                  ← optional
#
# Sender name/email may also be saved via Admin → Email Settings as a fallback,
# but the API key is NEVER stored in the database.

import resend
from resend.exceptions import (
    ResendError,
    InvalidApiKeyError,
    MissingApiKeyError,
    RateLimitError,
    ValidationError,
    MissingRequiredFieldsError,
)


def _resolve_resend_config(settings=None):
    """
    Build the Resend config dict.  Returns (cfg_dict, None) on success or
    (None, error_str) when required fields are missing.  Never raises.

    API key: env var RESEND_API_KEY only — never falls back to DB.
    Sender email/name: env var → DB fallback (not sensitive).
    """
    if settings is None:
        settings = EmailSettings.query.first()

    # API key: environment only — no DB fallback to prevent accidental exposure
    api_key = os.environ.get('RESEND_API_KEY', '').strip()

    # Sender identity: env var preferred, DB as convenience fallback
    sender_email = (os.environ.get('RESEND_SENDER_EMAIL', '').strip()
                    or (settings and settings.sender_email or ''))
    sender_name  = (os.environ.get('RESEND_SENDER_NAME', '').strip()
                    or (settings and settings.sender_name or '')
                    or 'HealthBridge NGO')

    if not api_key:
        return None, (
            'RESEND_API_KEY environment variable is not set. '
            'Add it in your Render / Railway dashboard under Environment Variables. '
            'Get a key at resend.com → API Keys.'
        )
    if not sender_email:
        return None, (
            'Sender email is not configured. '
            'Set RESEND_SENDER_EMAIL in your environment variables, '
            'or save it under Admin → Email Settings. '
            'It must belong to a domain verified in your Resend account.'
        )

    logger.debug(
        'Resend config — from=%s key=re_***%s (api key from env)',
        sender_email, api_key[-4:],
    )
    return {
        'api_key':      api_key,
        'sender_email': sender_email,
        'sender_name':  sender_name,
    }, None


def _validate_resend_config(cfg):
    """
    Validate API key format without making a network call.
    Returns (True, None) or (False, human_readable_error).  Never raises.
    """
    api_key = cfg.get('api_key', '')
    if not api_key:
        return False, 'RESEND_API_KEY environment variable is missing.'
    if not api_key.startswith('re_'):
        return False, (
            f'RESEND_API_KEY looks invalid — expected "re_..." '
            f'but got "{api_key[:6]}...". '
            'Regenerate it at resend.com → API Keys.'
        )
    logger.info('Resend pre-flight OK — key=re_***%s sender=%s',
                api_key[-4:], cfg.get('sender_email', ''))
    return True, None


def _send_single_email(cfg, to_email, to_name, subject, body_html):
    """
    Send one HTML email via Resend.
    Returns (True, None) on success or (False, human_readable_error) on any
    failure.  Never raises — safe to call in a loop from a Flask route.
    """
    logger.info('Resend → %s  subject="%s"  from=%s',
                to_email, subject[:60], cfg['sender_email'])

    resend.api_key = cfg['api_key']

    params: resend.Emails.SendParams = {
        'from':     f'{cfg["sender_name"]} <{cfg["sender_email"]}>',
        'to':       [to_email],
        'subject':  subject,
        'html':     body_html,
        'reply_to': cfg['sender_email'],
    }

    try:
        response = resend.Emails.send(params)
        email_id = getattr(response, 'id', None) or (
            response['id'] if isinstance(response, dict) else 'unknown'
        )
        logger.info('Resend delivered → %s  id=%s', to_email, email_id)
        return True, None

    except MissingApiKeyError:
        err = ('RESEND_API_KEY is missing. '
               'Set it in your Render / Railway environment variables.')
        logger.error('Resend MissingApiKeyError to %s', to_email)
        return False, err

    except InvalidApiKeyError as e:
        err = (f'Resend API key is invalid or revoked (HTTP 403). '
               f'Generate a new one at resend.com → API Keys. Detail: {e.message}')
        logger.error('Resend InvalidApiKeyError to %s: %s', to_email, e.message)
        return False, err

    except RateLimitError as e:
        err = (f'Resend rate limit reached sending to {to_email} (HTTP 429). '
               f'Slow the send rate or upgrade your Resend plan. Detail: {e.message}')
        logger.warning('Resend RateLimitError to %s: %s', to_email, e.message)
        return False, err

    except (ValidationError, MissingRequiredFieldsError) as e:
        err = (f'Resend rejected email parameters for {to_email}. '
               f'Ensure the sender domain is verified in Resend. Detail: {e.message}')
        logger.error('Resend ValidationError to %s: %s', to_email, e.message)
        return False, err

    except ResendError as e:
        err = (f'Resend API error to {to_email} '
               f'(code={e.code} type={e.error_type}): {e.message}')
        logger.error('ResendError to %s — code=%s: %s', to_email, e.code, e.message)
        return False, err

    except OSError as e:
        err = (f'Network error reaching Resend API for {to_email}: {e}. '
               'Check outbound HTTPS (port 443) is open in your environment.')
        logger.error('OSError (Resend HTTP) to %s: %s', to_email, e)
        return False, err

    except Exception as e:
        err = f'Unexpected error sending to {to_email}: {type(e).__name__}: {e}'
        logger.exception('Unexpected Resend error to %s', to_email)
        return False, err


def _send_welcome_email(to_email, to_name):
    """Fire-and-forget welcome email. Logs on failure, never raises."""
    cfg, err = _resolve_resend_config()
    if err:
        logger.info('Welcome email skipped for %s (Resend not configured): %s',
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
