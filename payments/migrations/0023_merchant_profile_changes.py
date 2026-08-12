from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [('payments', '0022_merchant_business_profile')]
    operations = [migrations.AddField(model_name='merchant', name='pending_profile_changes', field=models.JSONField(blank=True, default=dict))]
