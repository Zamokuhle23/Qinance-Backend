import uuid

from django.db import migrations, models
import django.db.models.deletion


def deduplicate_documents(apps, schema_editor):
    Document = apps.get_model('users', 'KYCDocument')
    seen = set()
    for document in Document.objects.order_by('-uploaded_at'):
        key = (document.user_id, document.document_type)
        if key in seen:
            document.delete()
        else:
            seen.add(key)


class Migration(migrations.Migration):
    dependencies = [('users', '0007_alter_otpverification_purpose_alter_user_role')]

    operations = [
        migrations.AlterField(
            model_name='kycdocument',
            name='document_type',
            field=models.CharField(choices=[('id', 'National ID'), ('passport', 'Passport'), ('residence', 'Proof of Residence'), ('income', 'Proof of Income'), ('selfie', 'Selfie'), ('selfie_front', 'Guided Selfie - Front'), ('selfie_left', 'Guided Selfie - Left'), ('selfie_right', 'Guided Selfie - Right')], max_length=20),
        ),
        migrations.AddField(
            model_name='kycdocument',
            name='capture_metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(deduplicate_documents, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='kycdocument',
            constraint=models.UniqueConstraint(fields=('user', 'document_type'), name='unique_user_kyc_document_type'),
        ),
        migrations.CreateModel(
            name='KYCVerification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('similarity_score', models.FloatField(blank=True, null=True)),
                ('pose_challenge_passed', models.BooleanField(default=False)),
                ('recommendation', models.CharField(choices=[('manual_review', 'Manual Review'), ('likely_match', 'Likely Match'), ('needs_attention', 'Needs Attention')], default='manual_review', max_length=30)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('evaluated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='kyc_verification', to='users.user')),
            ],
        ),
        migrations.AlterField(
            model_name='consentlog',
            name='consent_type',
            field=models.CharField(choices=[('terms_and_conditions', 'Terms and Conditions'), ('privacy_policy', 'Privacy Policy'), ('credit_bureau_check', 'Credit Bureau Check'), ('marketing_communications', 'Marketing Communications'), ('data_sharing', 'Data Sharing'), ('data_erasure_requested', 'Data Erasure Request'), ('biometric_identity_verification', 'Biometric Identity Verification')], max_length=40),
        ),
    ]
