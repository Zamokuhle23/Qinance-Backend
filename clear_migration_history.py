import os
import django
from django.db import connection

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

with connection.cursor() as cursor:
    # Delete the problematic migration records
    cursor.execute("DELETE FROM django_migrations WHERE app='payments' AND name='0014_merchant_embedding';")
    cursor.execute("DELETE FROM django_migrations WHERE app='campaigns' AND name='0001_initial';")
    print("Migration records cleared from remote database!")
