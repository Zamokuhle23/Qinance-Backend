from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
import shutil
import tempfile
from django.test import override_settings
from rest_framework.test import APITestCase

from payments.models import CardDetails, Customer, Merchant
from users.models import OTPVerification, User


TEST_MEDIA_ROOT = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', MEDIA_ROOT=TEST_MEDIA_ROOT)
class ManagedAccountAuthenticationTests(APITestCase):
    def setUp(self):
        self.password = 'StrongDemo123!'
        self.merchant_user = User.objects.create_user(
            phone='+26876123456',
            email='merchant@example.com',
            full_name='Test Merchant',
            password=self.password,
            role='merchant',
            kyc_status='approved',
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

    def test_public_registration_creates_inactive_profile_pending_kyc(self):
        payload = {
            'account_type': 'customer',
            'phone': '+26876999999',
            'email': 'public@example.com',
            'full_name': 'Public User',
            'national_id': '123456789',
            'password': self.password,
        }
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='public@example.com')
        self.assertEqual(user.role, 'customer')
        self.assertEqual(user.kyc_status, 'pending')
        self.assertFalse(Customer.objects.get(phone=user.phone).is_active)
        self.assertIsNotNone(user.registration_expires_at)

        resumed_otp = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(resumed_otp.status_code, 200)
        self.assertEqual(resumed_otp.data['resume_stage'], 'email_verification')
        otp = OTPVerification.objects.get(user=user, purpose='phone_verification', is_used=False)
        verified = self.client.post('/api/auth/verify-phone/', {'phone': user.phone, 'code': otp.code}, format='json')
        self.assertEqual(verified.status_code, 200)

        resumed_kyc = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(resumed_kyc.status_code, 200)
        self.assertEqual(resumed_kyc.data['resume_stage'], 'identity_verification')
        self.assertIn('access', resumed_kyc.data)
        self.assertEqual(resumed_kyc.data['kyc_uploaded'], [])
        self.assertEqual(self.client.post('/api/merchants/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/customers/', {}, format='json').status_code, 403)

    def test_admin_approves_completed_identity_application_and_email_is_sent(self):
        phone = '+26876999998'
        registered = self.client.post('/api/auth/register/', {
            'account_type': 'merchant',
            'phone': phone,
            'email': 'applicant@example.com',
            'full_name': 'Applicant Shop',
            'national_id': 'ID-9988',
            'password': self.password,
            'business_type': 'Retail',
            'location': 'Mbabane',
        }, format='json')
        self.assertEqual(registered.status_code, 201)
        applicant = User.objects.get(phone=phone)
        otp = OTPVerification.objects.get(user=applicant, purpose='phone_verification', is_used=False)
        verified = self.client.post('/api/auth/verify-phone/', {'phone': phone, 'code': otp.code}, format='json')
        self.assertEqual(verified.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {verified.data["access"]}')
        for document_type in ('id', 'selfie_front', 'selfie_left', 'selfie_right'):
            upload = self.client.post('/api/auth/kyc/upload/', {
                'document_type': document_type,
                'biometric_consent': 'true',
                'file': SimpleUploadedFile(f'{document_type}.jpg', b'fake-image', content_type='image/jpeg'),
            }, format='multipart')
            self.assertEqual(upload.status_code, 200)
        applicant.refresh_from_db()
        self.assertEqual(applicant.kyc_status, 'under_review')

        admin = User.objects.create_superuser(
            phone='+26876000001', email='reviewer@example.com', password=self.password, full_name='Reviewer'
        )
        self.client.force_authenticate(admin)
        approved = self.client.post(f'/api/auth/kyc/{applicant.id}/approve/')
        self.assertEqual(approved.status_code, 200)
        applicant.refresh_from_db()
        self.assertEqual(applicant.kyc_status, 'approved')
        merchant = Merchant.objects.get(phone=phone)
        self.assertTrue(merchant.is_active)
        self.assertTrue(merchant.kyc_approved)
        self.assertTrue(any(message.to == ['applicant@example.com'] and 'approved' in message.subject.lower() for message in mail.outbox))

    def test_cleanup_removes_only_expired_unfinished_registration(self):
        response = self.client.post('/api/auth/register/', {
            'account_type': 'customer', 'phone': '+26876999997',
            'email': 'stale@example.com', 'full_name': 'Stale Applicant',
            'national_id': 'STALE-1', 'password': self.password,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(phone='+26876999997')
        user.registration_expires_at = timezone.now() - timedelta(minutes=1)
        user.save(update_fields=['registration_expires_at'])
        call_command('cleanup_stale_registrations')
        self.assertFalse(User.objects.filter(phone='+26876999997').exists())
        self.assertFalse(Customer.objects.filter(phone='+26876999997').exists())

    def test_password_recovery_resumes_unfinished_application(self):
        phone = '+26876999996'
        email = 'recover@example.com'
        registered = self.client.post('/api/auth/register/', {
            'account_type': 'customer', 'phone': phone, 'email': email,
            'full_name': 'Recover Applicant', 'national_id': 'RECOVER-1',
            'password': self.password,
        }, format='json')
        self.assertEqual(registered.status_code, 201)

        requested = self.client.post('/api/auth/forgot-password/', {
            'identifier': email,
        }, format='json')
        self.assertEqual(requested.status_code, 200)
        user = User.objects.get(email=email)
        otp = OTPVerification.objects.get(user=user, purpose='password_reset', is_used=False)
        new_password = 'NewStrongDemo456!'
        reset = self.client.post('/api/auth/reset-password/', {
            'identifier': email, 'code': otp.code, 'new_password': new_password,
        }, format='json')
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.data['resume_stage'], 'identity_verification')
        self.assertIn('access', reset.data)
        self.assertEqual(reset.data['kyc_uploaded'], [])
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))

        resumed = self.client.post('/api/auth/register/', {
            'account_type': 'customer', 'phone': phone, 'email': email,
            'full_name': 'Recover Applicant', 'national_id': 'RECOVER-1',
            'password': new_password,
        }, format='json')
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.data['resume_stage'], 'identity_verification')
