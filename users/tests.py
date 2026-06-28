from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from payments.models import CardDetails, Customer, Merchant
from users.models import OTPVerification, User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ManagedAccountAuthenticationTests(APITestCase):
    def setUp(self):
        self.password = 'StrongDemo123!'
        self.merchant_user = User.objects.create_user(
            phone='+26876123456',
            email='merchant@example.com',
            full_name='Test Merchant',
            password=self.password,
            role='merchant',
        )
        self.merchant = Merchant.objects.create(
            phone=self.merchant_user.phone,
            name='Test Merchant',
            is_active=True,
            kyc_approved=True,
        )

    def test_password_login_returns_assigned_role_and_profile(self):
        response = self.client.post('/api/auth/login/', {
            'identifier': self.merchant_user.email,
            'password': self.password,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['role'], 'merchant')
        self.assertEqual(response.data['merchant_id'], str(self.merchant.id))

    def test_web_login_requires_emailed_otp(self):
        start = self.client.post('/api/auth/web-login/start/', {
            'identifier': self.merchant_user.email,
            'password': self.password,
        }, format='json')
        self.assertEqual(start.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        otp = OTPVerification.objects.get(user=self.merchant_user, purpose='web_login', is_used=False)

        verify = self.client.post('/api/auth/web-login/verify/', {
            'identifier': self.merchant_user.email,
            'code': otp.code,
        }, format='json')
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.data['role'], 'merchant')
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    def test_only_super_admin_can_create_linked_accounts(self):
        denied = self.client.post('/api/auth/admin/accounts/', {}, format='json')
        self.assertEqual(denied.status_code, 401)

        admin = User.objects.create_superuser(
            phone='+26876000000', email='admin@example.com', password=self.password, full_name='Admin'
        )
        self.client.force_authenticate(admin)
        created = self.client.post('/api/auth/admin/accounts/', {
            'account_type': 'customer',
            'full_name': 'New Customer',
            'email': 'customer@example.com',
            'phone': '+26876234567',
            'password': self.password,
            'bank': 'fnb',
            'credit_limit': '2500.00',
        }, format='json')
        self.assertEqual(created.status_code, 201)
        user = User.objects.get(email='customer@example.com')
        customer = Customer.objects.get(phone=user.phone)
        self.assertEqual(user.role, 'customer')
        self.assertTrue(CardDetails.objects.filter(customer=customer).exists())

    def test_public_registration_is_disabled(self):
        response = self.client.post('/api/auth/register/', {
            'phone': '+26876999999',
            'email': 'public@example.com',
            'full_name': 'Public User',
            'password': self.password,
        }, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post('/api/merchants/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/customers/', {}, format='json').status_code, 403)
