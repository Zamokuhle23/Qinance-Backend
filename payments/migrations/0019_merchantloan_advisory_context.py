from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('payments', '0018_publicholiday'),
    ]

    operations = [
        migrations.AddField(
            model_name='merchantloan',
            name='monthly_revenue',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AddField(
            model_name='merchantloan',
            name='monthly_expenses',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AddField(
            model_name='merchantloan',
            name='years_operating',
            field=models.DecimalField(decimal_places=1, default=Decimal('0.0'), max_digits=5),
        ),
        migrations.AddField(
            model_name='merchantloan',
            name='employees_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='merchantloan',
            name='collateral_description',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
