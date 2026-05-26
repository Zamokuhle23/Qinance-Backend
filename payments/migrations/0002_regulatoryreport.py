import uuid
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegulatoryReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('report_type', models.CharField(choices=[('ctr', 'Cash Transaction Report'), ('str', 'Suspicious Transaction Report')], max_length=10)),
                ('customer_phone', models.CharField(max_length=20)),
                ('customer_name', models.CharField(max_length=255)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('session_id', models.CharField(blank=True, max_length=100)),
                ('flag_details', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending_submission', 'Pending Submission'), ('submitted', 'Submitted to FIU'), ('acknowledged', 'Acknowledged by FIU')], default='pending_submission', max_length=30)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
