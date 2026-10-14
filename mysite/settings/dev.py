from .base import *



# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True
URL_MONITORING = os.getenv('URL_MONITORING', 'http://127.0.0.1:8000/api/monitoring/')

ALLOWED_HOSTS = ['192.168.0.132', '127.0.0.1']


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Local checks must never send real Firebase notifications.
FCM_ENABLED = False

# Lokalne uruchomienie kolejki nie może wysłać prawdziwej poczty.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
