import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0001_initial'),
        ('users', '0002_user_daily_transaction_limit_user_fraud_flags_and_more'),
    ]

    operations = [

        # RegulatoryReport
        migrations.CreateModel(
            name='RegulatoryReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('report_type', models.CharField(choices=[('ctr', 'Cash Transaction Report'), ('str', 'Suspicious Transaction Report')], max_length=10)),
                ('customer_phone', models.CharField(max_length=20)),
                ('customer_name', models.CharField(max_length=255)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('session_id', models.CharField(blank=True, max_length=100)),
                ('flag_details', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending_submission', 'Pending Submission'), ('submitted', 'Submitted to FIU'), ('acknowledged', 'Acknowledged by FIU')], default='pending_submission', max_length=30)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={'ordering': ['-created_at']},
        ),

        # MerchantDocument
        migrations.CreateModel(
            name='MerchantDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='payments.merchant')),
                ('document_type', models.CharField(choices=[('business_registration', 'Business Registration'), ('tax_clearance', 'Tax Clearance Certificate'), ('bank_letter', 'Bank Confirmation Letter'), ('id_owner', 'Owner ID / Passport'), ('other', 'Other')], max_length=30)),
                ('file', models.FileField(upload_to='merchant_kyc/')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('uploaded_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),

        # Merchant risk_rating + kyc_approved fields
        migrations.AddField(
            model_name='merchant',
            name='risk_rating',
            field=models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='low', max_length=10),
        ),
        migrations.AddField(
            model_name='merchant',
            name='kyc_approved',
            field=models.BooleanField(default=False),
        ),

        # KYCDocument expiry_date
        migrations.AddField(
            model_name='kycdocument',
            name='expiry_date',
            field=models.DateField(blank=True, null=True),
            preserve_default=True,
        ),

        # ConsentLog
        migrations.CreateModel(
            name='ConsentLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='consents', to=settings.AUTH_USER_MODEL)),
                ('consent_type', models.CharField(choices=[('terms_and_conditions', 'Terms and Conditions'), ('privacy_policy', 'Privacy Policy'), ('credit_bureau_check', 'Credit Bureau Check'), ('marketing_communications', 'Marketing Communications'), ('data_sharing', 'Data Sharing'), ('data_erasure_requested', 'Data Erasure Request')], max_length=40)),
                ('accepted', models.BooleanField(default=True)),
                ('ip_address', models.GenericIPAddressField()),
                ('user_agent', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
