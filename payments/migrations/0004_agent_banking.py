from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility node: agent banking models are already created in 0001."""

    dependencies = [
        ('payments', '0003_compliance_models'),
    ]

    operations = []
