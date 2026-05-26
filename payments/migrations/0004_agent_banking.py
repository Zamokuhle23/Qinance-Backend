import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_compliance_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('transaction_type', models.CharField(
                    choices=[
                        ('cashback', 'Cash Back — Bank to Cash'),
                        ('bank_deposit', 'Bank Deposit — Cash to Bank'),
                    ], max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('fee', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('merchant_incentive', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('confirmed', 'Confirmed'),
                        ('failed', 'Failed'),
                        ('cancelled', 'Cancelled'),
                    ], default='pending', max_length=20)),
                ('bank', models.CharField(blank=True, max_length=100)),
                ('reference', models.CharField(blank=True, max_length=100)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('customer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='agent_transactions',
                    to='payments.customer')),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='agent_transactions',
                    to='payments.merchant')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='MerchantAgentProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('is_cashback_enabled', models.BooleanField(default=False)),
                ('is_bank_deposit_enabled', models.BooleanField(default=False)),
                ('available_cash_float', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('daily_cashback_limit', models.DecimalField(decimal_places=2, default=5000, max_digits=10)),
                ('total_cashback_today', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('last_reset_date', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('merchant', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='agent_profile',
                    to='payments.merchant')),
            ],
        ),
    ]
