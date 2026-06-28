import uuid
import random
import hashlib

from datetime import timedelta

from django.db import models
from django.utils import timezone

from django.contrib.auth.models import (
    AbstractBaseUser,
    PermissionsMixin,
    BaseUserManager,
)

from django.contrib.auth.hashers import (
    make_password,
    check_password,
)


class UserManager(BaseUserManager):

    def create_user(self, phone, password=None, **extra_fields):

        if not phone:
            raise ValueError("Phone number required")

        user = self.model(
            phone=phone,
            **extra_fields
        )

        if password:
            user.set_password(password)

        user.save(using=self._db)

        return user

    def create_superuser(self, phone, password=None, **extra_fields):

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "super_admin")
        extra_fields.setdefault("kyc_status", "approved")

        return self.create_user(
            phone,
            password,
            **extra_fields
        )


class User(AbstractBaseUser, PermissionsMixin):

    ROLES = [
        ('customer', 'Customer'),
        ('merchant', 'Merchant'),
        ('support', 'Support'),
        ('kyc_officer', 'KYC Officer'),
        ('credit_officer', 'Credit Officer'),
        ('fraud_analyst', 'Fraud Analyst'),
        ('super_admin', 'Super Admin'),
    ]

    KYC_STATUS = [
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    CREDIT_STATUS = [
        ('not_requested', 'Not Requested'),
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    RISK_LEVELS = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('blocked', 'Blocked'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    phone = models.CharField(
        max_length=20,
        unique=True
    )

    email = models.EmailField(
        unique=True,
        blank=True,
        default=''
    )

    full_name = models.CharField(
        max_length=255
    )

    national_id = models.CharField(
        max_length=100,
        blank=True
    )

    role = models.CharField(
        max_length=30,
        choices=ROLES,
        default='support'
    )

    pin = models.CharField(
        max_length=255,
        blank=True
    )

    is_phone_verified = models.BooleanField(
        default=False
    )

    # Transaction limits
    daily_transaction_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=5000
    )

    monthly_transaction_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=50000
    )

    # Risk & fraud
    risk_level = models.CharField(
        max_length=20,
        choices=RISK_LEVELS,
        default='low'
    )

    fraud_flags = models.IntegerField(
        default=0
    )

    # KYC & credit workflow
    kyc_status = models.CharField(
        max_length=20,
        choices=KYC_STATUS,
        default='pending'
    )

    credit_status = models.CharField(
        max_length=20,
        choices=CREDIT_STATUS,
        default='not_requested'
    )

    rejection_reason = models.TextField(
        blank=True
    )

    is_active = models.BooleanField(
        default=True
    )

    is_staff = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    USERNAME_FIELD = 'phone'

    REQUIRED_FIELDS = ['email']

    objects = UserManager()

    def set_pin(self, raw_pin):
        self.pin = make_password(raw_pin)

    def check_pin(self, raw_pin):
        return check_password(raw_pin, self.pin)

    def __str__(self):
        return self.phone


class KYCDocument(models.Model):

    DOCUMENT_TYPES = [
        ('id', 'National ID'),
        ('passport', 'Passport'),
        ('residence', 'Proof of Residence'),
        ('income', 'Proof of Income'),
        ('selfie', 'Selfie'),
    ]

    STATUS = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='documents'
    )

    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPES
    )

    file = models.FileField(
        upload_to='kyc/'
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='pending'
    )

    rejection_reason = models.TextField(
        blank=True
    )

    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text='Document expiry date — required for ID and passport'
    )

    uploaded_at = models.DateTimeField(
        auto_now_add=True
    )


class CreditApplication(models.Model):

    STATUS = [
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='credit_application'
    )

    employer_name = models.CharField(
        max_length=255
    )

    monthly_income = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    requested_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    approved_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='pending'
    )

    rejection_reason = models.TextField(
        blank=True
    )

    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_credit_applications'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )


class FraudFlag(models.Model):

    FLAG_TYPES = [
        ('velocity', 'High Velocity'),
        ('device', 'Device Mismatch'),
        ('location', 'Location Risk'),
        ('limit', 'Limit Exceeded'),
        ('manual', 'Manual Review'),
    ]

    STATUS = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='risk_flags'
    )

    flag_type = models.CharField(
        max_length=30,
        choices=FLAG_TYPES
    )

    description = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='open'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )


class OTPVerification(models.Model):

    PURPOSES = [
        ('phone_verification', 'Phone Verification'),
        ('login', 'Login'),
        ('transaction', 'Transaction'),
        ('password_reset', 'Password Reset'),
        ('pin_reset', 'PIN Reset'),
        ('web_login', 'Web Login'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    code = models.CharField(
        max_length=6
    )

    purpose = models.CharField(
        max_length=30,
        choices=PURPOSES
    )

    is_used = models.BooleanField(
        default=False
    )

    expires_at = models.DateTimeField()

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    @classmethod
    def generate_otp(cls, user, purpose):

        code = str(
            random.randint(100000, 999999)
        )

        otp = cls.objects.create(
            user=user,
            code=code,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(minutes=5)
        )

        return otp

    @classmethod
    def verify(cls, user, code, purpose):
        """Returns (success: bool, message: str)"""
        try:
            otp = cls.objects.filter(
                user=user,
                code=code,
                purpose=purpose,
                is_used=False,
                expires_at__gt=timezone.now()
            ).latest('created_at')

            otp.is_used = True
            otp.save()
            return True, "Verified"

        except cls.DoesNotExist:
            return False, "Invalid or expired OTP"


class TrustedDevice(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='trusted_devices'
    )

    fingerprint = models.CharField(
        max_length=255
    )

    device_name = models.CharField(
        max_length=255
    )

    ip_address = models.GenericIPAddressField()

    user_agent = models.TextField()

    is_trusted = models.BooleanField(
        default=True
    )

    last_seen = models.DateTimeField(
        auto_now=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    @staticmethod
    def generate_fingerprint(request):

        raw = (
            request.META.get('REMOTE_ADDR', '') +
            request.META.get('HTTP_USER_AGENT', '')
        )

        return hashlib.sha256(
            raw.encode()
        ).hexdigest()


class AuditLog(models.Model):

    ACTIONS = [
        ('login', 'Login'),
        ('pin_login', 'PIN Login'),
        ('kyc_upload', 'KYC Upload'),
        ('kyc_approved', 'KYC Approved'),
        ('kyc_rejected', 'KYC Rejected'),
        ('credit_approved', 'Credit Approved'),
        ('credit_rejected', 'Credit Rejected'),
        ('otp_verified', 'OTP Verified'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='audit_logs'
    )

    action = models.CharField(
        max_length=50,
        choices=ACTIONS
    )

    ip_address = models.GenericIPAddressField()

    device_fingerprint = models.CharField(
        max_length=255,
        blank=True
    )

    metadata = models.JSONField(
        default=dict,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    @classmethod
    def log(
        cls,
        user,
        action,
        request,
        metadata=None
    ):

        cls.objects.create(
            user=user,
            action=action,
            ip_address=request.META.get(
                'REMOTE_ADDR',
                '0.0.0.0'
            ),
            device_fingerprint=TrustedDevice.generate_fingerprint(
                request
            ),
            metadata=metadata or {},
        )


class FCMDevice(models.Model):
    """Stores FCM push tokens so the backend can notify customers of payment confirmations."""
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fcm_devices')
    token      = models.TextField(unique=True)
    device_id  = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class ConsentLog(models.Model):

    CONSENT_TYPES = [
        ('terms_and_conditions',     'Terms and Conditions'),
        ('privacy_policy',           'Privacy Policy'),
        ('credit_bureau_check',      'Credit Bureau Check'),
        ('marketing_communications', 'Marketing Communications'),
        ('data_sharing',             'Data Sharing'),
        ('data_erasure_requested',   'Data Erasure Request'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='consents')
    consent_type = models.CharField(max_length=40, choices=CONSENT_TYPES)
    accepted     = models.BooleanField(default=True)
    ip_address   = models.GenericIPAddressField()
    user_agent   = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.phone} — {self.consent_type} — {'accepted' if self.accepted else 'declined'}"

    class Meta:
        ordering = ['-created_at']
