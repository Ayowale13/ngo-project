import os
import csv
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from flask import (Flask, render_template, redirect, url_for,
                   flash, request, Response, session)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)

from config import Config
from models import db, AdminUser, Subscriber, Volunteer, EmailSettings

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


# ── DB + seed ────────────────────────────────────────────────────────────────

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
            settings = EmailSettings(
                sender_name='NGO Outreach Team',
                smtp_host='smtp.gmail.com',
                smtp_port=587,
                mail_service='smtp'
            )
            db.session.add(settings)
            db.session.commit()


# ── Public routes ────────────────────────────────────────────────────────────

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
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        skills = request.form.get('skills', '').strip()
        message = request.form.get('message', '').strip()

        if not full_name or not email or not phone:
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('volunteer'))

        volunteer_entry = Volunteer(
            full_name=full_name,
            email=email,
            phone=phone,
            skills=skills,
            message=message
        )
        db.session.add(volunteer_entry)
        db.session.commit()
        flash('Thank you for signing up to volunteer! We will contact you soon.', 'success')
        return redirect(url_for('volunteer'))

    return render_template('volunteer.html')


@app.route('/subscribe', methods=['POST'])
def subscribe():
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()

    if not full_name or not email:
        flash('Name and email are required for subscription.', 'danger')
        return redirect(request.referrer or url_for('index'))

    existing = Subscriber.query.filter_by(email=email).first()
    if existing:
        flash('This email is already subscribed to our newsletter.', 'info')
        return redirect(request.referrer or url_for('index'))

    subscriber = Subscriber(full_name=full_name, email=email, phone=phone or None)
    db.session.add(subscriber)
    db.session.commit()

    # Attempt to send welcome email using saved settings
    _send_welcome_email(email, full_name)

    flash('You have successfully subscribed to our newsletter!', 'success')
    return redirect(request.referrer or url_for('index'))


# ── Admin routes ─────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        admin = AdminUser.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            login_user(admin, remember=False)
            flash('Welcome back!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('admin_login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    subscribers = Subscriber.query.order_by(Subscriber.date_subscribed.desc()).all()
    volunteers = Volunteer.query.order_by(Volunteer.date_submitted.desc()).all()
    stats = {
        'total_subscribers': Subscriber.query.count(),
        'total_volunteers': Volunteer.query.count(),
        'new_this_month': Subscriber.query.filter(
            db.extract('month', Subscriber.date_subscribed) == datetime.utcnow().month,
            db.extract('year', Subscriber.date_subscribed) == datetime.utcnow().year
        ).count(),
        'volunteers_this_month': Volunteer.query.filter(
            db.extract('month', Volunteer.date_submitted) == datetime.utcnow().month,
            db.extract('year', Volunteer.date_submitted) == datetime.utcnow().year
        ).count(),
    }
    return render_template('admin_dashboard.html',
                           subscribers=subscribers,
                           volunteers=volunteers,
                           stats=stats)


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
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Full Name', 'Email', 'Phone', 'Date Subscribed'])
    for s in subscribers:
        writer.writerow([s.id, s.full_name, s.email, s.phone or '',
                         s.date_subscribed.strftime('%Y-%m-%d %H:%M')])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=subscribers.csv'}
    )


@app.route('/admin/export/volunteers')
@login_required
def export_volunteers():
    volunteers = Volunteer.query.order_by(Volunteer.date_submitted.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Full Name', 'Email', 'Phone', 'Skills', 'Message', 'Date Submitted'])
    for v in volunteers:
        writer.writerow([v.id, v.full_name, v.email, v.phone,
                         v.skills or '', v.message or '',
                         v.date_submitted.strftime('%Y-%m-%d %H:%M')])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=volunteers.csv'}
    )


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
        settings.sender_name = request.form.get('sender_name', '').strip()
        settings.smtp_host = request.form.get('smtp_host', '').strip()
        port_str = request.form.get('smtp_port', '587').strip()
        settings.smtp_port = int(port_str) if port_str.isdigit() else 587
        settings.mail_service = request.form.get('mail_service', 'smtp')

        smtp_password = request.form.get('smtp_password', '').strip()
        if smtp_password:
            settings.set_smtp_password(smtp_password)

        api_key = request.form.get('api_key', '').strip()
        if api_key:
            settings.set_api_key(api_key)

        settings.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Email settings saved successfully.', 'success')
        return redirect(url_for('admin_email_settings'))

    return render_template('admin_email_settings.html', settings=settings)


@app.route('/admin/send-newsletter', methods=['GET', 'POST'])
@login_required
def admin_send_newsletter():
    settings = EmailSettings.query.first()
    subscribers = Subscriber.query.all()

    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()

        if not subject or not body:
            flash('Subject and body are required.', 'danger')
            return redirect(url_for('admin_send_newsletter'))

        sent, failed = 0, 0
        for sub in subscribers:
            success = _send_email(sub.email, sub.full_name, subject, body, settings)
            if success:
                sent += 1
            else:
                failed += 1

        flash(f'Newsletter sent: {sent} delivered, {failed} failed.', 'success' if failed == 0 else 'warning')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_send_newsletter.html', settings=settings, count=len(subscribers))


# ── Email helpers ────────────────────────────────────────────────────────────

def _send_email(to_email, to_name, subject, body_html, settings=None):
    if settings is None:
        settings = EmailSettings.query.first()
    if not settings or not settings.sender_email or not settings.smtp_host:
        return False

    smtp_password = settings.get_smtp_password()
    if not smtp_password:
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f'{settings.sender_name} <{settings.sender_email}>'
        msg['To'] = f'{to_name} <{to_email}>'
        part = MIMEText(body_html, 'html')
        msg.attach(part)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.sender_email, smtp_password)
            server.sendmail(settings.sender_email, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'Email error to {to_email}: {e}')
        return False


def _send_welcome_email(to_email, to_name):
    settings = EmailSettings.query.first()
    if not settings or not settings.sender_email:
        return
    subject = f'Welcome to {settings.sender_name or "Our NGO"} Newsletter!'
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1e3a5f;">
    <h2>Welcome, {to_name}!</h2>
    <p>Thank you for subscribing to our newsletter. You'll receive updates on our
    health outreach programs and charity initiatives.</p>
    <p>Together we can make a difference.</p>
    <br><p>— {settings.sender_name or 'The NGO Team'}</p>
    </body></html>
    """
    _send_email(to_email, to_name, subject, body, settings)


# ── Entry point ──────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    app.run(debug=True)
