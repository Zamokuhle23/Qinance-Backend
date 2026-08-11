from django.db import migrations, models
from decimal import Decimal

class Migration(migrations.Migration):
    dependencies = [('payments', '0020_merchantloan_async_advisory')]
    operations = [
        migrations.AddField(model_name='merchantloan', name='offer_status', field=models.CharField(default='awaiting_admin', max_length=20)),
        migrations.AddField(model_name='merchantloan', name='offer_minimum', field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
        migrations.AddField(model_name='merchantloan', name='offer_maximum', field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
    ]
