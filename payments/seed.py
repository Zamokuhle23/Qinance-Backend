import os
import django
from datetime import date, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from payments.models import Merchant, Customer, CardDetails, CreditStatement, DebitMandate
from users.models import User

Merchant.objects.all().delete()
Customer.objects.all().delete()

merchants = [
    {'name': 'Nhlangano Furniture', 'business_type': 'Furniture', 'phone': '+26876100001', 'location': 'Nhlangano', 'is_active': True, 'kyc_approved': True},
    {'name': 'Manzini Hardware', 'business_type': 'Hardware', 'phone': '+26876100002', 'location': 'Manzini', 'is_active': True, 'kyc_approved': True},
    {'name': 'Mbabane Pharmacy', 'business_type': 'Pharmacy', 'phone': '+26876100003', 'location': 'Mbabane', 'is_active': True, 'kyc_approved': True},
    {'name': 'Siteki Clothing', 'business_type': 'Clothing', 'phone': '+26876100004', 'location': 'Siteki', 'is_active': True, 'kyc_approved': True},
    {'name': "Pigg's Peak Agri", 'business_type': 'Agriculture', 'phone': '+26876100005', 'location': "Pigg's Peak", 'is_active': True, 'kyc_approved': True},
]
for index, m in enumerate(merchants, start=1):
    Merchant.objects.create(**m)
    user, _ = User.objects.update_or_create(
        phone=m['phone'],
        defaults={
            'full_name': m['name'],
            'email': f'merchant{index}@qinance.demo',
            'kyc_status': 'approved',
            'is_active': True,
        },
    )
    user.set_pin('1234')
    user.save(update_fields=['pin'])

customers = [
    {'phone': '+26876200001', 'full_name': 'Sipho Dlamini', 'bank': 'fnb', 'credit_limit': 5000, 'current_balance': 1250},
    {'phone': '+26876200002', 'full_name': 'Nomvula Nkosi', 'bank': 'standard', 'credit_limit': 3000, 'current_balance': 0},
    {'phone': '+26876200003', 'full_name': 'Bongani Simelane', 'bank': 'nedbank', 'credit_limit': 2000, 'current_balance': 800},
    {'phone': '+26876200004', 'full_name': 'Thandi Masuku', 'bank': 'eswatini_bank', 'credit_limit': 4000, 'current_balance': 200},
]

today = date.today()
period_start = today.replace(day=1)
period_end = (period_start.replace(month=period_start.month % 12 + 1, day=1) if period_start.month < 12
              else period_start.replace(year=period_start.year + 1, month=1, day=1)) - timedelta(days=1)
due_date = period_end + timedelta(days=15)

for c in customers:
    balance = c.pop('current_balance')
    customer = Customer.objects.create(**c, current_balance=balance, statement_balance=balance)

    # Create virtual card
    CardDetails.objects.create(customer=customer)

    # Create open statement if balance exists
    if balance > 0:
        CreditStatement.objects.create(
            customer=customer,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            opening_balance=0,
            total_purchases=balance,
            closing_balance=balance,
            minimum_payment=max(round(float(balance) * 0.10, 2), 50),
            status='open',
        )

    # Create debit mandate
    DebitMandate.objects.create(customer=customer, bank=customer.bank)

print(f"Created {len(merchants)} active merchants (demo PIN: 1234)")
print(f"Created {len(customers)} customers with virtual cards")
print("\nMerchant IDs:")
for m in Merchant.objects.all():
    print(f"  {m.name}: {m.id}")
print("\nCustomer test phones:")
for c in Customer.objects.all():
    print(f"  {c.full_name}: {c.phone} | Credit: E{c.credit_limit} | Balance: E{c.current_balance} | Available: E{c.available_credit}")
