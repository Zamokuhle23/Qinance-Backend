import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from users.models import User
from .models import CardDetails, Customer, LinkedAccount, Merchant, WalletEntry
from .routing import get_wallet, post_wallet_entry
from .consumers import PaymentSessionConsumer


class PaymentSessionConsumerTests(SimpleTestCase):
    async def test_confirmation_accepts_current_broadcast_payload(self):
        consumer = PaymentSessionConsumer()
        messages = []

        async def capture_send(*, text_data=None, bytes_data=None, close=False):
            messages.append(json.loads(text_data))

        consumer.send = capture_send
        await consumer.payment_confirmed({
            'type': 'payment_confirmed',
            'session_id': 'session-id',
            'amount': '10.00',
            'funding_mode': 'wallet',
            'bank_used': '',
            'customer_phone': '76000001',
        })

        self.assertEqual(messages[0]['type'], 'payment_confirmed')
        self.assertEqual(messages[0]['funding_mode'], 'wallet')


class PaymentRoutingTests(APITestCase):
    def setUp(self):
        self.customer_user = User.objects.create_user(
            phone='76000001', email='customer@example.com', password='StrongPass123!',
            full_name='Customer', role='customer', kyc_status='approved', credit_status='approved',
        )
        self.customer_user.set_pin('1234')
        self.customer_user.save(update_fields=['pin'])
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
            'pin': '1234',
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
            'pin': '1234',
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
            'pin': '1234',
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
            'pin': '1234',
            'funding_mode': 'wallet',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_payment_rejects_an_incorrect_transaction_pin(self):
        self.client.force_authenticate(self.merchant_user)
        created = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '10.00',
        }, format='json')
        self.client.force_authenticate(self.customer_user)
        response = self.client.post('/api/sessions/confirm/', {
            'session_id': created.data['session_id'],
            'customer_phone': self.customer.phone,
            'pin': '9999',
            'funding_mode': 'wallet',
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('Incorrect transaction PIN', response.data['error'])

    def test_customer_cannot_read_another_customer_profile(self):
        other_user = User.objects.create_user(
            phone='76000004', email='private@example.com', password='StrongPass123!',
            full_name='Private Customer', role='customer', kyc_status='approved',
        )
        Customer.objects.create(phone=other_user.phone, full_name=other_user.full_name)
        self.client.force_authenticate(other_user)
        response = self.client.get(f'/api/customers/{self.customer.phone}/')
        self.assertEqual(response.status_code, 403)

    def test_wallet_only_customer_does_not_receive_a_credit_card(self):
        self.customer_user.credit_status = 'pending'
        self.customer_user.save(update_fields=['credit_status'])
        self.client.force_authenticate(self.customer_user)
        response = self.client.get(f'/api/card/{self.customer.phone}/')
        self.assertEqual(response.status_code, 404)

    def test_customer_static_session_does_not_expire_merchant_session(self):
        self.client.force_authenticate(self.merchant_user)
        merchant_session = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '10.00',
        }, format='json')
        self.client.force_authenticate(self.customer_user)
        customer_session = self.client.post('/api/sessions/create/', {
            'merchant_id': str(self.merchant.id), 'amount': '20.00',
        }, format='json')
        self.assertEqual(customer_session.status_code, 201)
        self.assertEqual(
            self.client.get(f"/api/sessions/{merchant_session.data['session_id']}/").data['status'],
            'waiting',
        )

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

    def test_merchant_can_apply_for_and_list_a_loan(self):
        self.client.force_authenticate(self.merchant_user)
        created = self.client.post('/api/merchant/loans/', {
            'requested_amount': '5000.00',
            'repayment_frequency': 'weekly',
            'purpose': 'Buy stock',
        }, format='json')

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['status'], 'pending')
        self.assertEqual(created.data['requested_amount'], '5000.00')
        self.assertEqual(created.data['interest_rate'], '20.00')
        self.assertEqual(created.data['estimated_interest'], Decimal('1000.00'))
        self.assertEqual(created.data['estimated_total_repayment'], Decimal('6000.00'))
        self.assertEqual(created.data['estimated_installments'], 26)
        self.assertEqual(created.data['estimated_installment'], Decimal('230.77'))

        listed = self.client.get('/api/merchant/loans/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(listed.data[0]['purpose'], 'Buy stock')

    def test_merchant_cannot_open_two_loan_applications(self):
        self.client.force_authenticate(self.merchant_user)
        application = {
            'requested_amount': '5000.00',
            'repayment_frequency': 'biweekly',
            'purpose': 'Buy stock',
        }
        self.assertEqual(
            self.client.post('/api/merchant/loans/', application, format='json').status_code,
            201,
        )
        duplicate = self.client.post('/api/merchant/loans/', application, format='json')
        self.assertEqual(duplicate.status_code, 400)

    def test_customer_cannot_access_merchant_loans(self):
        self.client.force_authenticate(self.customer_user)
        response = self.client.get('/api/merchant/loans/')
        self.assertEqual(response.status_code, 403)
