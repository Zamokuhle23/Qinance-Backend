from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('payments', '0019_merchantloan_advisory_context')]

    operations = [
        migrations.RemoveField(model_name='merchantloan', name='collateral_description'),
        migrations.AddField(
            model_name='merchantloan', name='advisory_status',
            field=models.CharField(default='queued', max_length=20),
        ),
        migrations.AddField(
            model_name='merchantloan', name='advisory_result',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
