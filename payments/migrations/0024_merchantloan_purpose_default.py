from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('payments', '0023_merchant_profile_changes')]

    operations = [
        migrations.AlterField(
            model_name='merchantloan',
            name='purpose',
            field=models.CharField(blank=True, default='', max_length=250),
        ),
    ]
