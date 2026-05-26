import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0008_agent_session'),
    ]

    operations = [
        migrations.CreateModel(
            name='LinkedMoMoAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('msisdn', models.CharField(max_length=20)),
                ('status', models.CharField(choices=[('active', 'Active'), ('suspended', 'Suspended')], default='active', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='momo_account', to='payments.customer')),
            ],
        ),
        migrations.CreateModel(
            name='MoMoTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reference_id', models.CharField(max_length=64, unique=True)),
                ('txn_type', models.CharField(choices=[('collection', 'Collection — debit customer'), ('disbursement', 'Disbursement — credit customer')], max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('msisdn', models.CharField(max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('successful', 'Successful'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='momo_transactions', to='payments.customer')),
                ('related_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='momo_transactions', to='payments.paymentsession')),
                ('related_agent_txn', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='momo_transactions', to='payments.agenttransaction')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
