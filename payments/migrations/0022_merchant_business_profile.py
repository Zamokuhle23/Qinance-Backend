from decimal import Decimal
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('payments', '0021_merchantloan_admin_offer')]
    operations = [
        migrations.AddField(model_name='merchant', name='monthly_revenue', field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
        migrations.AddField(model_name='merchant', name='monthly_expenses', field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
        migrations.AddField(model_name='merchant', name='years_operating', field=models.DecimalField(decimal_places=1, default=Decimal('0.0'), max_digits=5)),
        migrations.AddField(model_name='merchant', name='employees_count', field=models.PositiveIntegerField(default=0)),
        migrations.RemoveField(model_name='merchantloan', name='monthly_revenue'),
        migrations.RemoveField(model_name='merchantloan', name='monthly_expenses'),
        migrations.RemoveField(model_name='merchantloan', name='years_operating'),
        migrations.RemoveField(model_name='merchantloan', name='employees_count'),
    ]
