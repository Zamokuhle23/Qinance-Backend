import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_merge_0002_regulatoryreport_0004_agent_banking'),
    ]

    operations = [

        # Trust fields on Merchant
        migrations.AddField(
            model_name='merchant',
            name='trust_level',
            field=models.CharField(
                choices=[('untrusted','Untrusted'),('standard','Standard'),('trusted','Trusted'),('anchor','Anchor')],
                default='untrusted', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='merchant',
            name='trust_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='merchant',
            name='dispute_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='merchant',
            name='transaction_count',
            field=models.IntegerField(default=0),
        ),

        # CustomerDeviceSecret
        migrations.CreateModel(
            name='CustomerDeviceSecret',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('secret', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('last_synced', models.DateTimeField(auto_now=True)),
                ('customer', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='device_secret',
                    to='payments.customer',
                )),
            ],
        ),

        # PendingSettlement
        migrations.CreateModel(
            name='PendingSettlement',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('trust_score', models.IntegerField(default=0)),
                ('status', models.CharField(
                    choices=[
                        ('pending','Pending'),('settled','Settled'),
                        ('cancelled','Cancelled'),('reversed','Reversed'),
                    ],
                    default='pending', max_length=20,
                )),
                ('received_at',   models.DateTimeField(default=django.utils.timezone.now)),
                ('settle_after',  models.DateTimeField()),
                ('settled_at',    models.DateTimeField(blank=True, null=True)),
                ('conflict_note', models.TextField(blank=True)),
                ('customer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pending_settlements',
                    to='payments.customer',
                )),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pending_settlements',
                    to='payments.merchant',
                )),
            ],
            options={'ordering': ['-trust_score', 'received_at']},
        ),
    ]
