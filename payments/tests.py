from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from users.models import User
from .models import CardDetails, Customer, LinkedAccount, Merchant, WalletEntry
from .routing import get_wallet, post_wallet_entry


class PaymentRoutingTests(APITestCase):
    def setUp(self):
        self.customer_user = User.objects.create_user(
            phone='76000001', email='customer@example.com', password='StrongPass123!',
            full_name='Customer', role='customer', kyc_status='approved', credit_status='approved',
        )
        self.customer = Customer.objects.create(
            phone=self.customer_user.phone, full_name='Customer', credit_limit=Decimal('1000.00'),
        )
        CardDetails.objects.create(customer=self.customer)
        self.merchant_user = User.objects.create_user(
            phone='76000002', email='merchant@example.com', password='StrongPass123!',
            full_name='Merchant', role='merchant', kyc_status='approved',
        )
        self.merchant = Merchant.objects.create(
            phone=self.merchant_user.phone, name='Merchant', is_active=True, kyc_approved=True,
        )
        self.customer_account = LinkedAccount.objects.create(
            customer=self.customer, provider='fnb', display_name='FNB Eswatini', account_last4='1234',
        )
        self.merchant_account = LinkedAccount.objects.create(
            merchant=self.merchant, provider='standard', display_name='Standard Bank',
            account_last4='5678', can_debit=False,
        )

    def test_routing_profile_only_exposes_credit_when_approved(self):
        self.client.force_authenticate(self.customer_user)
        response = self.client.get('/api/routing/profile/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['credit']['eligible'])

        self.customer_user.credit_status = 'pending'
        self.customer_user.save(update_fields=['credit_status'])
        response = self.client.get('/api/routing/profile/')
        self.assertFalse(response.data['credit']['eligible'])

    def test_default_and_transaction_override_are_separate(self):
        self.client.force_authenticate(self.customer_user)
        response = self.client.patch('/api/routing/profile/', {
            'default_type': 'linked',
            'default_account_id': str(self.customer_account.id),
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.default_payment_source, 'linked')

        self.client.force_authenticate(self.merchant_user)
        response = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id),
            'amount': '25.00',
            'settlement_destination': 'wallet',
        }, format='json')
        session_id = response.data['session_id']

        self.client.force_authenticate(self.customer_user)
        response = self.client.post('/api/sessions/confirm/', {
            'session_id': session_id,
            'customer_phone': self.customer.phone,
            'funding_mode': 'wallet',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Insufficient wallet balance', response.data['error'])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.default_payment_source, 'linked')

    def test_wallet_payment_moves_exact_amount(self):
        customer_wallet = get_wallet(self.customer)
        post_wallet_entry(customer_wallet, Decimal('100.00'), 'topup', 'Test funding', 'test:topup')

        self.client.force_authenticate(self.merchant_user)
        created = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '35.50',
        }, format='json')
        self.assertEqual(created.status_code, 201)

        self.client.force_authenticate(self.customer_user)
        confirmed = self.client.post('/api/sessions/confirm/', {
            'session_id': created.data['session_id'],
            'customer_phone': self.customer.phone,
            'funding_mode': 'wallet',
        }, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        customer_wallet.refresh_from_db()
        merchant_wallet = get_wallet(self.merchant)
        merchant_wallet.refresh_from_db()
        self.assertEqual(customer_wallet.balance, Decimal('64.50'))
        self.assertEqual(merchant_wallet.balance, Decimal('35.50'))

    def test_credit_is_rejected_when_not_approved(self):
        self.customer_user.credit_status = 'pending'
        self.customer_user.save(update_fields=['credit_status'])
        self.client.force_authenticate(self.merchant_user)
        created = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '10.00',
        }, format='json')
        self.client.force_authenticate(self.customer_user)
        response = self.client.post('/api/sessions/confirm/', {
            'session_id': created.data['session_id'],
            'customer_phone': self.customer.phone,
            'funding_mode': 'credit',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not available', response.data['error'])

    def test_customer_cannot_authorise_another_customers_payment(self):
        other_user = User.objects.create_user(
            phone='76000003', email='other@example.com', password='StrongPass123!',
            full_name='Other', role='customer', kyc_status='approved',
        )
        self.client.force_authenticate(self.merchant_user)
        created = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '10.00',
        }, format='json')
        self.client.force_authenticate(other_user)
        response = self.client.post('/api/sessions/confirm/', {
            'session_id': created.data['session_id'],
            'customer_phone': self.customer.phone,
            'funding_mode': 'wallet',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_ledger_entries_cannot_be_changed_or_deleted(self):
        entry = post_wallet_entry(get_wallet(self.customer), Decimal('50.00'), 'topup', 'Test', 'immutable:test')
        entry.amount = Decimal('500.00')
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            WalletEntry.objects.filter(pk=entry.pk).update(reference='tampered')
        with self.assertRaises(ValidationError):
            WalletEntry.objects.filter(pk=entry.pk).delete()
        self.assertEqual(WalletEntry.objects.get(pk=entry.pk).amount, Decimal('50.00'))
