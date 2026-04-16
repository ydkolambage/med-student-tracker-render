import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
RUNNING_TESTS = 'test' in sys.argv
ENVIRONMENT = os.environ.get('DJANGO_ENV', 'test' if RUNNING_TESTS else 'dev').strip().lower() or 'dev'
if ENVIRONMENT not in {'dev', 'test', 'prod'}:
    raise ImproperlyConfigured('DJANGO_ENV must be one of: dev, test, prod.')

IS_PRODUCTION = ENVIRONMENT == 'prod'
IS_TEST = ENVIRONMENT == 'test'
IS_DEVELOPMENT = ENVIRONMENT == 'dev'


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


IS_RENDER_FREE_TEST = env_bool('DJANGO_RENDER_FREE_TEST', default=False)

def env_int(name, default):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be an integer.') from exc


def env_list(name, default=''):
    raw_value = os.environ.get(name, default)
    return [item.strip() for item in raw_value.split(',') if item.strip()]


def env_path(name, default):
    raw_value = os.environ.get(name)
    value = Path(raw_value if raw_value not in (None, '') else default).expanduser()
    if not value.is_absolute():
        value = BASE_DIR / value
    return value.resolve(strict=False)


def require_env(name):
    value = os.environ.get(name)
    if value in (None, ''):
        raise ImproperlyConfigured(f'{name} must be set.')
    return value


def validate_external_storage_path(path_value, *, setting_name):
    if not path_value.is_absolute():
        raise ImproperlyConfigured(f'{setting_name} must be an absolute path.')
    try:
        path_value.relative_to(BASE_DIR)
    except ValueError:
        return path_value
    raise ImproperlyConfigured(f'{setting_name} must be outside the application tree in production.')


SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set in production.')
    SECRET_KEY = 'dev-only-change-me'

DEBUG = env_bool('DJANGO_DEBUG', default=not IS_PRODUCTION and not IS_TEST)
default_allowed_hosts = 'localhost,127.0.0.1,testserver'
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', default_allowed_hosts if not IS_PRODUCTION else '')
render_external_hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if render_external_hostname and render_external_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(render_external_hostname)
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS must be set in production.')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')
if render_external_hostname:
    render_origin = f'https://{render_external_hostname}'
    if render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(render_origin)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'backups',
    'students',
    'results',
    'imports',
    'audits',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

default_use_sqlite = IS_DEVELOPMENT or IS_TEST
USE_SQLITE = env_bool('DJANGO_USE_SQLITE', default=default_use_sqlite)
if IS_PRODUCTION and USE_SQLITE:
    raise ImproperlyConfigured('SQLite cannot be used in production.')

if USE_SQLITE:
    sqlite_default = BASE_DIR / ('test.sqlite3' if IS_TEST else 'db.sqlite3')
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': env_path('SQLITE_PATH', sqlite_default),
        }
    }
else:
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        import dj_database_url

        DATABASES = {
            'default': dj_database_url.config(
                default=database_url,
                conn_max_age=env_int('DJANGO_DB_CONN_MAX_AGE', 60 if IS_PRODUCTION else 0),
                ssl_require=env_bool('DJANGO_DB_SSL_REQUIRE', default=IS_PRODUCTION),
            )
        }
    else:
        database_name = os.environ.get('POSTGRES_DB', 'medtracker')
        database_user = os.environ.get('POSTGRES_USER', '')
        database_password = os.environ.get('POSTGRES_PASSWORD', '')
        database_host = os.environ.get('POSTGRES_HOST', '127.0.0.1')
        database_port = os.environ.get('POSTGRES_PORT', '5432')
        if IS_PRODUCTION:
            database_name = require_env('POSTGRES_DB')
            database_user = require_env('POSTGRES_USER')
            database_password = require_env('POSTGRES_PASSWORD')
            database_host = require_env('POSTGRES_HOST')
            database_port = require_env('POSTGRES_PORT')
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': database_name,
                'USER': database_user,
                'PASSWORD': database_password,
                'HOST': database_host,
                'PORT': database_port,
                'CONN_MAX_AGE': env_int('DJANGO_DB_CONN_MAX_AGE', 60 if IS_PRODUCTION else 0),
            }
        }

backup_database = DATABASES['default']
if 'postgresql' not in backup_database.get('ENGINE', ''):
    backup_database = {
        'NAME': os.environ.get('BACKUP_POSTGRES_DB', os.environ.get('POSTGRES_DB', 'medtracker')),
        'USER': os.environ.get('BACKUP_POSTGRES_USER', os.environ.get('POSTGRES_USER', '')),
        'PASSWORD': os.environ.get('BACKUP_POSTGRES_PASSWORD', os.environ.get('POSTGRES_PASSWORD', '')),
        'HOST': os.environ.get('BACKUP_POSTGRES_HOST', os.environ.get('POSTGRES_HOST', '127.0.0.1')),
        'PORT': os.environ.get('BACKUP_POSTGRES_PORT', os.environ.get('POSTGRES_PORT', '5432')),
    }

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('DJANGO_TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = env_path('DJANGO_STATIC_ROOT', BASE_DIR / 'staticfiles')
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

MEDIA_URL = os.environ.get('DJANGO_MEDIA_URL', '/media/')
default_media_root = BASE_DIR / 'media' if not IS_PRODUCTION else Path('/var/lib/med-student-tracker/media')
MEDIA_ROOT = env_path('DJANGO_MEDIA_ROOT', default_media_root)
PROTECTED_EXPORT_ROOT = env_path('DJANGO_PROTECTED_EXPORT_ROOT', MEDIA_ROOT / 'protected_exports')

default_backup_full_root = Path('/home/ydkolambage/projects/Backups') if not IS_PRODUCTION else Path('/home/ydkolambage/projects/Backups')
default_backup_daily_root = BASE_DIR / 'var' / 'backups' / 'daily_sql' if not IS_PRODUCTION else Path('/var/backups/med-student-tracker/daily_sql')
BACKUP_FULL_ROOT = env_path('BACKUP_FULL_ROOT', default_backup_full_root)
BACKUP_DAILY_SQL_ROOT = env_path('BACKUP_DAILY_SQL_ROOT', default_backup_daily_root)
BACKUP_PROJECT_ROOT = env_path('BACKUP_PROJECT_ROOT', BASE_DIR)
BACKUP_PG_DUMP_BINARY = os.environ.get('BACKUP_PG_DUMP_BINARY', 'pg_dump')
BACKUP_PSQL_BINARY = os.environ.get('BACKUP_PSQL_BINARY', 'psql')
BACKUP_TIME_ZONE = os.environ.get('BACKUP_TIME_ZONE', TIME_ZONE)
BACKUP_DATABASE_NAME = os.environ.get('BACKUP_POSTGRES_DB', backup_database.get('NAME', ''))
BACKUP_DATABASE_USER = os.environ.get('BACKUP_POSTGRES_USER', backup_database.get('USER', ''))
BACKUP_DATABASE_PASSWORD = os.environ.get('BACKUP_POSTGRES_PASSWORD', backup_database.get('PASSWORD', ''))
BACKUP_DATABASE_HOST = os.environ.get('BACKUP_POSTGRES_HOST', str(backup_database.get('HOST', '')))
BACKUP_DATABASE_PORT = os.environ.get('BACKUP_POSTGRES_PORT', str(backup_database.get('PORT', '')))
BACKUP_RETENTION_DAYS = env_int('BACKUP_RETENTION_DAYS', 30)
BACKUP_VERIFY_RESTORE_QUERY = os.environ.get('BACKUP_VERIFY_RESTORE_QUERY', 'SELECT 1;')
BACKUP_EXCLUDED_DIR_NAMES = (
    '.git',
    '.venv',
    '__pycache__',
    'node_modules',
    'Backups_daily_sql',
    'media',
    'staticfiles',
    'var',
)
BACKUP_EXCLUDED_FILE_SUFFIXES = (
    '.pyc',
    '.pyo',
    '.tmp',
    '.temp',
    '.swp',
    '.swo',
    '.log',
    '.pid',
    '.sqlite3',
)
BACKUP_EXCLUDED_FILE_NAMES = ('.DS_Store',)

if IS_PRODUCTION and not IS_RENDER_FREE_TEST:
    for setting_name, setting_value in (
        ('DJANGO_MEDIA_ROOT', MEDIA_ROOT),
        ('DJANGO_PROTECTED_EXPORT_ROOT', PROTECTED_EXPORT_ROOT),
        ('BACKUP_FULL_ROOT', BACKUP_FULL_ROOT),
        ('BACKUP_DAILY_SQL_ROOT', BACKUP_DAILY_SQL_ROOT),
    ):
        validate_external_storage_path(setting_value, setting_name=setting_name)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/admin/'

DEFAULT_FROM_EMAIL = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL', 'noreply@medtracker.local')
SERVER_EMAIL = os.environ.get('DJANGO_SERVER_EMAIL', DEFAULT_FROM_EMAIL)
default_email_backend = 'django.core.mail.backends.smtp.EmailBackend' if IS_PRODUCTION else 'django.core.mail.backends.console.EmailBackend'
EMAIL_BACKEND = os.environ.get('DJANGO_EMAIL_BACKEND', default_email_backend)
EMAIL_HOST = os.environ.get('DJANGO_EMAIL_HOST', 'localhost')
EMAIL_PORT = env_int('DJANGO_EMAIL_PORT', 25)
EMAIL_HOST_USER = os.environ.get('DJANGO_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('DJANGO_EMAIL_USE_TLS', default=False)
EMAIL_USE_SSL = env_bool('DJANGO_EMAIL_USE_SSL', default=False)
EMAIL_TIMEOUT = env_int('DJANGO_EMAIL_TIMEOUT', 10)

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if env_bool('DJANGO_USE_PROXY_SSL_HEADER', default=IS_PRODUCTION) else None
SESSION_COOKIE_SECURE = env_bool('DJANGO_SESSION_COOKIE_SECURE', default=IS_PRODUCTION)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool('DJANGO_SESSION_EXPIRE_AT_BROWSER_CLOSE', default=True)
CSRF_COOKIE_SECURE = env_bool('DJANGO_CSRF_COOKIE_SECURE', default=IS_PRODUCTION)
SECURE_HSTS_SECONDS = env_int('DJANGO_SECURE_HSTS_SECONDS', 31536000 if IS_PRODUCTION else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', default=IS_PRODUCTION)
SECURE_HSTS_PRELOAD = env_bool('DJANGO_SECURE_HSTS_PRELOAD', default=False)
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', default=IS_PRODUCTION)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'structured': {
            '()': 'config.logging.JsonFormatter',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'structured',
        },
    },
    'loggers': {
        'medtracker.audit': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_AUDIT_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
        },
    },
}

