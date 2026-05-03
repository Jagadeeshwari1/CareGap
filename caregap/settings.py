"""
CareGap Analytics — Django Settings
"""
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'caregap-dev-secret-key-change-in-production',
)
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'patients',
    'rag',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'caregap.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'caregap.wsgi.application'

ENTERPRISE_MODE = os.environ.get('ENTERPRISE_MODE', '0') == '1'

if ENTERPRISE_MODE:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'caregap_db',
            'USER': 'caregap_user',
            'PASSWORD': 'caregap_password',
            'HOST': 'localhost',
            'PORT': '5432',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
_static_dir = BASE_DIR / 'static'
STATICFILES_DIRS = [_static_dir]
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

CORS_ALLOW_ALL_ORIGINS = True

# ── File-based cache OR Enterprise Redis ──────
if ENTERPRISE_MODE:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': 'redis://localhost:6379/1',
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            }
        }
    }
    CELERY_BROKER_URL = 'redis://localhost:6379/2'
    CELERY_RESULT_BACKEND = 'redis://localhost:6379/3'
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': str(BASE_DIR / 'cache'),
            'TIMEOUT': 300,
            'OPTIONS': {'MAX_ENTRIES': 200},
        }
    }
    CELERY_BROKER_URL = 'memory://'
    CELERY_RESULT_BACKEND = 'db+sqlite:///results.sqlite'

# ── Synthea CSV data directory ─────────────────────────────────────
# Override by setting SYNTHEA_DATA_DIR env var, otherwise defaults
# to the relative path data/synthea_ca_seed43438_p30000/
SYNTHEA_DATA_DIR = os.environ.get(
    'SYNTHEA_DATA_DIR',
    str(BASE_DIR / 'data' / 'synthea_ca_seed43438_p30000'),
)

# ── HuggingFace Inference API ──────────────────────────────────────
HF_API_TOKEN = os.environ.get('HF_API_TOKEN', '')

# ── Ollama / LLaMA settings ───────────────────────────────────────
OLLAMA_BASE_URL = 'http://localhost:11434'
OLLAMA_MODEL    = 'phi3'

# ── Gemini API (Fallback) ─────────────────────────────────────────
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# ── FAISS vector index path ────────────────────────────────────────
FAISS_INDEX_PATH = BASE_DIR / 'rag' / 'faiss_index'
# trigger reload
