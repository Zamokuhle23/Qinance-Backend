from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0008_kyc_assisted_verification')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='registration_expires_at',
            field=models.DateTimeField(blank=True, help_text='Expiry for unfinished public onboarding; null for managed or submitted accounts', null=True),
        ),
    ]
