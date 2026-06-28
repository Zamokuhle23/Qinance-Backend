from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility node: RegulatoryReport is already created in 0001."""

    dependencies = [
        ('payments', '0001_initial'),
    ]

    operations = []
