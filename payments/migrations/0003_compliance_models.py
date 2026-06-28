from django.db import migrations


class Migration(migrations.Migration):
    """
    Compatibility node for an older branch of the migration graph.

    RegulatoryReport, MerchantDocument, and merchant compliance fields are
    already created in payments.0001_initial. KYCDocument.expiry_date and
    ConsentLog belong to users and are created by users.0004.
    """

    dependencies = [
        ('payments', '0001_initial'),
        ('users', '0002_user_daily_transaction_limit_user_fraud_flags_and_more'),
    ]

    operations = []
