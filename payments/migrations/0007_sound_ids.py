from django.db import migrations, models


def backfill_sound_ids(apps, schema_editor):
    Customer = apps.get_model('payments', 'Customer')
    Merchant = apps.get_model('payments', 'Merchant')
    for i, c in enumerate(Customer.objects.order_by('created_at'), 1):
        c.sound_id = i
        c.save(update_fields=['sound_id'])
    for i, m in enumerate(Merchant.objects.order_by('created_at'), 1):
        m.sound_id = i
        m.save(update_fields=['sound_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_sound_payment'),
    ]

    operations = [
        migrations.AddField(
            model_name='merchant',
            name='sound_id',
            field=models.PositiveIntegerField(db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='customer',
            name='sound_id',
            field=models.PositiveIntegerField(db_index=True, null=True, unique=True),
        ),
        migrations.RunPython(backfill_sound_ids, migrations.RunPython.noop),
    ]
