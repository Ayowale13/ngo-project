from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import base64
import os

db = SQLAlchemy()


class AdminUser(UserMixin, db.Model):
    __tablename__ = 'admin_users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<AdminUser {self.username}>'


class Subscriber(db.Model):
    __tablename__ = 'subscribers'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    date_subscribed = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Subscriber {self.email}>'


class Volunteer(db.Model):
    __tablename__ = 'volunteers'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    skills = db.Column(db.String(200), nullable=True)
    message = db.Column(db.Text, nullable=True)
    date_submitted = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Volunteer {self.email}>'


class EmailSettings(db.Model):
    __tablename__ = 'email_settings'
    id = db.Column(db.Integer, primary_key=True)
    sender_email = db.Column(db.String(120), nullable=True)
    sender_name = db.Column(db.String(120), nullable=True)
    smtp_host = db.Column(db.String(120), nullable=True)
    smtp_port = db.Column(db.Integer, nullable=True, default=587)
    smtp_password_enc = db.Column(db.Text, nullable=True)  # base64-encoded, masked in UI
    mail_service = db.Column(db.String(20), default='smtp')  # smtp | brevo | mailchimp
    api_key_enc = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_smtp_password(self, password):
        if password:
            self.smtp_password_enc = base64.b64encode(password.encode()).decode()

    def get_smtp_password(self):
        if self.smtp_password_enc:
            return base64.b64decode(self.smtp_password_enc.encode()).decode()
        return None

    def set_api_key(self, key):
        if key:
            self.api_key_enc = base64.b64encode(key.encode()).decode()

    def get_api_key(self):
        if self.api_key_enc:
            return base64.b64decode(self.api_key_enc.encode()).decode()
        return None
