import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'sapphire-nightfall-whisper-secret-2024'

    # On Render, DATABASE_URL is set automatically when you attach a Postgres db.
    # Locally, falls back to SQLite.
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL') or
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'database.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── SMTP / email (read by _resolve_smtp_config in app.py) ─────────────────
    # Set these as environment variables on Render (or in .env locally).
    # They are NOT used directly by Flask-Mail; app.py's helpers read them via
    # os.environ so they also override any values stored in the DB.
    #
    # SMTP_SENDER_EMAIL   your-ngo@gmail.com
    # SMTP_SENDER_NAME    HealthBridge NGO
    # SMTP_HOST           smtp.gmail.com
    # SMTP_PORT           587
    # SMTP_PASSWORD       your-16-char-app-password
