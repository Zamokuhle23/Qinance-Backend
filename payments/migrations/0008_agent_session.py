import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0007_sound_ids'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('transaction_type', models.CharField(choices=[('cashback', 'Cash Back — Bank to Cash'), ('bank_deposit', 'Bank Deposit — Cash to Bank')], max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled'), ('expired', 'Expired')], default='pending', max_length=20)),
                ('channel', models.CharField(choices=[('qr', 'QR Code'), ('nfc', 'NFC')], default='qr', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_sessions', to='payments.merchant')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='agent_sessions', to='payments.customer')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
