from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import connections


def database_health():
    connection = connections['default']
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        row = cursor.fetchone()
    return {
        'ok': row == (1,),
        'engine': settings.DATABASES['default']['ENGINE'],
    }


def media_storage_health():
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    probe_path = media_root / f'.healthcheck-{uuid4().hex}.tmp'
    probe_path.write_text('ok', encoding='utf-8')
    probe_contents = probe_path.read_text(encoding='utf-8')
    probe_path.unlink(missing_ok=True)
    return {
        'ok': probe_contents == 'ok',
        'media_root': str(media_root),
    }
