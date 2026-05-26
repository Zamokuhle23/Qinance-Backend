import uuid
import random
import hashlib

from django.db import models
from django.utils import timezone


# ── Card helpers ──────────────────────────────────────────────────────────────

def generate_card_number():
    return '4' + ''.join([str(random.randint(0, 9)) for _ in range(15)])


def _hash_cvv(raw_cvv):
    """Hash CVV on save — PCI-DSS 3.2.1: never store plaintext CVV."""
    from django.conf import settings
    salt = getattr(settings, 'SECRET_KEY', 'qinance-salt')
    return hashlib.sha256(f'{salt}{raw_cvv}'.encode()).hexdigest()


def generate_cvv():
    raw = ''.join([str(random.randint(0, 9)) for _ in range(3)])
    return _hash_cvv(raw)


def default_expiry():
    return timezone.now().date().replace(year=timezone.now().year + 3)


# ── Merchant ──────────────────────────────────────────────────────────────────

class Merchant(models.Model):

    RISK_RATINGS = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    id                = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name              = models.CharField(max_length=200)
    business_type     = models.CharField(max_length=100, blank=True)
    phone             = models.CharField(max_length=20, unique=True)
    location          = models.CharField(max_length=200, blank=True)
    is_active         = models.BooleanField(default=False)
    risk_rating       = models.CharField(max_length=10, choices=RISK_RATINGS, default='low')
    kyc_approved      = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)
    trust_level       = models.CharField(max_length=20, choices=[('untrusted','Untrusted'),('standard','Standard'),('trusted','Trusted'),('anchor','Anchor')], default='untrusted')
    trust_score       = models.IntegerField(default=0)
    dispute_count     = models.IntegerField(default=0)
    transaction_count = models.IntegerField(default=0)
    sound_id          = models.PositiveIntegerField(unique=True, null=True, db_index=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class MerchantDocument(models.Model):

    DOC_TYPES = [
        ('business_registration', 'Business Registration'),
        ('tax_clearance', 'Tax Clearance Certificate'),
        ('bank_letter', 'Bank Confirmation Letter'),
        ('id_owner', 'Owner ID / Passport'),
        ('other', 'Other'),
    ]

    STATUS = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant      = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=30, choices=DOC_TYPES)
    file          = models.FileField(upload_to='merchant_kyc/')
    status        = models.CharField(max_length=20, choices=STATUS, default='pending')
    uploaded_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.merchant.name} — {self.document_type}'


# ── Customer ──────────────────────────────────────────────────────────────────

class Customer(models.Model):

    BANK_CHOICES = [
        ('fnb',           'FNB Eswatini'),
        ('standard',      'Standard Bank'),
        ('nedbank',       'Nedbank'),
        ('eswatini_bank', 'Eswatini Bank'),
    ]

    FUNDING_MODE_CHOICES = [
        ('credit', 'Qinance Credit Card'),
        ('bank',   'Bank Account (EPS)'),
        ('jit',    'JIT Bank to Card'),
    ]

    id                   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone                = models.CharField(max_length=20, unique=True)
    full_name            = models.CharField(max_length=200, blank=True)
    national_id          = models.CharField(max_length=50, blank=True)
    bank                 = models.CharField(max_length=100, blank=True, choices=BANK_CHOICES)
    default_funding_mode = models.CharField(max_length=20, choices=FUNDING_MODE_CHOICES, default='credit')

    credit_limit     = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    current_balance  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    statement_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    payment_due_date = models.DateField(null=True, blank=True)
    interest_rate    = models.DecimalField(max_digits=5, decimal_places=2, default=24.00)

    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sound_id   = models.PositiveIntegerField(unique=True, null=True, db_index=True)

    @property
    def available_credit(self):
        from decimal import Decimal
        return self.credit_limit - self.current_balance

    @property
    def minimum_payment(self):
        from decimal import Decimal
        if self.statement_balance <= 0:
            return Decimal('0.00')
        return max(self.statement_balance * Decimal('0.10'), Decimal('50.00'))

    @property
    def days_until_due(self):
        if not self.payment_due_date:
            return None
        return (self.payment_due_date - timezone.now().date()).days

    @property
    def is_overdue(self):
        if not self.payment_due_date:
            return False
        return timezone.now().date() > self.payment_due_date and self.statement_balance > 0

    def __str__(self):
        return f'{self.full_name or self.phone}'

    class Meta:
        ordering = ['-created_at']


# ── Card Details ──────────────────────────────────────────────────────────────

class CardDetails(models.Model):

    STATUS_CHOICES = [
        ('active',    'Active'),
        ('frozen',    'Frozen'),
        ('cancelled', 'Cancelled'),
        ('pending',   'Pending Activation'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer     = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='card_details')
    card_number  = models.CharField(max_length=16, default=generate_card_number)
    cvv          = models.CharField(max_length=64, default=generate_cvv)   # stores hash
    expiry_date  = models.DateField(default=default_expiry)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    bin_country  = models.CharField(max_length=50, default='ZA')
    card_network = models.CharField(max_length=20, default='Visa')
    created_at   = models.DateTimeField(auto_now_add=True)

    @property
    def masked_number(self):
        return f'**** **** **** {self.card_number[-4:]}'

    @property
    def expiry_display(self):
        return self.expiry_date.strftime('%m/%y')

    @property
    def expiry_month(self):
        return self.expiry_date.month

    @property
    def expiry_year(self):
        return self.expiry_date.year

    def verify_cvv(self, raw_cvv):
        return self.cvv == _hash_cvv(raw_cvv)

    def __str__(self):
        return f'{self.customer} — {self.masked_number}'


# ── Statements ────────────────────────────────────────────────────────────────

class CreditStatement(models.Model):

    STATUS_CHOICES = [
        ('open',         'Open'),
        ('closed',       'Closed — Unpaid'),
        ('paid_minimum', 'Minimum Paid'),
        ('paid_full',    'Paid in Full'),
        ('overdue',      'Overdue'),
    ]

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer         = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='statements')
    period_start     = models.DateField()
    period_end       = models.DateField()
    due_date         = models.DateField()
    opening_balance  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_purchases  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_payments   = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    interest_charged = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    closing_balance  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    minimum_payment  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at       = models.DateTimeField(auto_now_add=True)

    @property
    def days_until_due(self):
        return (self.due_date - timezone.now().date()).days

    def __str__(self):
        return f'{self.customer} — {self.period_start} to {self.period_end} — {self.status}'

    class Meta:
        ordering = ['-period_end']


# ── Transactions ──────────────────────────────────────────────────────────────

class CreditTransaction(models.Model):

    TYPE_CHOICES = [
        ('purchase',   'Purchase'),
        ('repayment',  'Repayment'),
        ('interest',   'Interest Charge'),
        ('fee',        'Fee'),
        ('refund',     'Refund'),
    ]

    FUNDING_CHOICES = [
        ('credit', 'Qinance Credit'),
        ('bank',   'Bank Transfer (EPS)'),
        ('jit',    'JIT Bank to Card'),
    ]

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer         = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='credit_transactions')
    merchant         = models.ForeignKey(Merchant, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    statement        = models.ForeignKey(CreditStatement, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    session          = models.ForeignKey('PaymentSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='credit_transactions')
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    funding_mode     = models.CharField(max_length=30, choices=FUNDING_CHOICES, blank=True)
    amount           = models.DecimalField(max_digits=10, decimal_places=2)
    description      = models.CharField(max_length=300, blank=True)
    reference        = models.CharField(max_length=100, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.customer} — {self.transaction_type} — E{self.amount}'

    class Meta:
        ordering = ['-created_at']


# ── Payment Sessions ──────────────────────────────────────────────────────────

class PaymentSession(models.Model):

    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('waiting',   'Waiting for Customer'),
        ('confirmed', 'Confirmed'),
        ('failed',    'Failed'),
        ('expired',   'Expired'),
    ]

    FUNDING_CHOICES = [
        ('credit', 'Qinance Credit Card'),
        ('bank',   'Bank Transfer (EPS)'),
        ('jit',    'JIT Bank to Card'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant     = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='sessions')
    customer     = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    funding_mode = models.CharField(max_length=30, choices=FUNDING_CHOICES, blank=True)
    bank_used    = models.CharField(max_length=100, blank=True)
    jit_funded   = models.BooleanField(default=False)
    jit_bank     = models.CharField(max_length=100, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.merchant.name} — E{self.amount} — {self.status}'

    class Meta:
        ordering = ['-created_at']


# ── Debit Mandate ─────────────────────────────────────────────────────────────

class DebitMandate(models.Model):

    BANK_CHOICES = [
        ('fnb',           'FNB Eswatini'),
        ('standard',      'Standard Bank'),
        ('nedbank',       'Nedbank'),
        ('eswatini_bank', 'Eswatini Bank'),
    ]

    STATUS_CHOICES = [
        ('active',    'Active'),
        ('cancelled', 'Cancelled'),
        ('suspended', 'Suspended'),
    ]

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer       = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='mandates')
    bank           = models.CharField(max_length=100, choices=BANK_CHOICES)
    account_number = models.CharField(max_length=50, blank=True)
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    authorised_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.customer} — {self.bank} — {self.status}'

    class Meta:
        ordering = ['-authorised_at']


# ── Repayments ────────────────────────────────────────────────────────────────

class Repayment(models.Model):

    METHOD_CHOICES = [
        ('eps_transfer', 'EPS Bank Transfer'),
        ('debit_order',  'Debit Order'),
        ('cash',         'Cash at Branch'),
    ]

    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed',    'Failed'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer     = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='repayments')
    statement    = models.ForeignKey(CreditStatement, on_delete=models.SET_NULL, null=True, blank=True, related_name='repayments')
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    method       = models.CharField(max_length=20, choices=METHOD_CHOICES, default='eps_transfer')
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reference    = models.CharField(max_length=100, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.customer} — E{self.amount} — {self.status}'

    class Meta:
        ordering = ['-created_at']


# ── Agent Banking ──────────────────────────────────────────────────────────────

class AgentTransaction(models.Model):
    """
    Records all agent banking transactions.
    cashback:     customer withdraws cash from merchant till — debited from bank
    bank_deposit: customer deposits cash at merchant — credited to bank via EPS
    No Qinance wallet involved in either direction.
    """

    TRANSACTION_TYPES = [
        ('cashback',     'Cash Back — Bank to Cash'),
        ('bank_deposit', 'Bank Deposit — Cash to Bank'),
    ]

    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed',    'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    id                 = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant           = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='agent_transactions')
    customer           = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='agent_transactions')
    transaction_type   = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount             = models.DecimalField(max_digits=10, decimal_places=2)
    fee                = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    merchant_incentive = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    bank               = models.CharField(max_length=100, blank=True)
    reference          = models.CharField(max_length=100, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)
    confirmed_at       = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.transaction_type} — {self.customer} — E{self.amount}'

    class Meta:
        ordering = ['-created_at']


class AgentSession(models.Model):
    """
    Merchant-created session for QR/NFC agent banking.
    Customer scans/taps and confirms from their app.
    """

    TRANSACTION_TYPES = [
        ('cashback',     'Cash Back — Bank to Cash'),
        ('bank_deposit', 'Bank Deposit — Cash to Bank'),
    ]
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('expired',   'Expired'),
    ]
    CHANNEL_CHOICES = [
        ('qr',  'QR Code'),
        ('nfc', 'NFC'),
    ]

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant         = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='agent_sessions')
    customer         = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='agent_sessions')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount           = models.DecimalField(max_digits=10, decimal_places=2)
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    channel          = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default='qr')
    created_at       = models.DateTimeField(auto_now_add=True)
    confirmed_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.transaction_type} — {self.merchant.name} — E{self.amount} — {self.status}'

    class Meta:
        ordering = ['-created_at']


class MerchantAgentProfile(models.Model):
    """
    Tracks merchant agent banking settings and available cash float.
    Merchant sets how much cash they have available for cashback.
    For bank deposits, merchant receives cash — no float limit needed.
    """

    merchant                 = models.OneToOneField(Merchant, on_delete=models.CASCADE, related_name='agent_profile')
    is_cashback_enabled      = models.BooleanField(default=False)
    is_bank_deposit_enabled  = models.BooleanField(default=False)
    available_cash_float     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_cashback_limit     = models.DecimalField(max_digits=10, decimal_places=2, default=5000)
    total_cashback_today     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    last_reset_date          = models.DateField(null=True, blank=True)
    created_at               = models.DateTimeField(auto_now_add=True)

    def reset_daily_totals_if_needed(self):
        today = timezone.now().date()
        if self.last_reset_date != today:
            self.total_cashback_today = 0
            self.last_reset_date      = today
            self.save()

    def __str__(self):
        return f'{self.merchant.name} — Agent Profile'


# ── Regulatory Reports (CTR / STR) ────────────────────────────────────────────

class RegulatoryReport(models.Model):

    REPORT_TYPES = [
        ('ctr', 'Cash Transaction Report'),
        ('str', 'Suspicious Transaction Report'),
    ]

    STATUS = [
        ('pending_submission', 'Pending Submission'),
        ('submitted',          'Submitted to FIU'),
        ('acknowledged',       'Acknowledged by FIU'),
    ]

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_type     = models.CharField(max_length=10, choices=REPORT_TYPES)
    customer_phone  = models.CharField(max_length=20)
    customer_name   = models.CharField(max_length=255)
    amount          = models.DecimalField(max_digits=12, decimal_places=2)
    session_id      = models.CharField(max_length=100, blank=True)
    flag_details    = models.TextField(blank=True)
    status          = models.CharField(max_length=30, choices=STATUS, default='pending_submission')
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.report_type.upper()} — {self.customer_phone} — E{self.amount}'

    class Meta:
        ordering = ['-created_at']


# ── MTN MoMo ──────────────────────────────────────────────────────────────────

class LinkedMoMoAccount(models.Model):
    """
    Customer's linked MTN MoMo wallet.
    MSISDN stored normalised without + (e.g. 26876123456).
    """
    STATUS_CHOICES = [
        ('active',    'Active'),
        ('suspended', 'Suspended'),
    ]

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer   = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='momo_account')
    msisdn     = models.CharField(max_length=20)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.customer.phone} — MoMo {self.msisdn}'


class MoMoTransaction(models.Model):
    """
    Tracks async MTN MoMo API calls so the app can poll for status.
    reference_id is the UUID returned by the MTN API (X-Reference-Id header).
    """
    TYPE_CHOICES = [
        ('collection',   'Collection — debit customer'),
        ('disbursement', 'Disbursement — credit customer'),
    ]
    STATUS_CHOICES = [
        ('pending',    'Pending'),
        ('successful', 'Successful'),
        ('failed',     'Failed'),
    ]

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer            = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='momo_transactions')
    reference_id        = models.CharField(max_length=64, unique=True)
    txn_type            = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount              = models.DecimalField(max_digits=10, decimal_places=2)
    msisdn              = models.CharField(max_length=20)
    status              = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    related_session     = models.ForeignKey('PaymentSession',  on_delete=models.SET_NULL, null=True, blank=True, related_name='momo_transactions')
    related_agent_txn   = models.ForeignKey('AgentTransaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='momo_transactions')
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.txn_type} — {self.customer.phone} — E{self.amount} — {self.status}'

    class Meta:
        ordering = ['-created_at']


# ── Sound / Contactless Payment ───────────────────────────────────────────────

class CustomerDeviceSecret(models.Model):
    """
    Shared secret synced once at onboarding for offline token generation.
    Stored in Android Keystore on device. Never sent again after initial sync.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer    = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='device_secret')
    secret      = models.CharField(max_length=64)
    created_at  = models.DateTimeField(auto_now_add=True)
    last_synced = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.customer.phone} — device secret'


class PendingSettlement(models.Model):
    """
    Sound payment sits here for 30 seconds before EPS executes.
    Allows trust-based conflict resolution if same token submitted twice.
    Higher trust merchant wins. First submission wins ties.
    """

    STATUS = [
        ('pending',   'Pending — awaiting settlement window'),
        ('settled',   'Settled — EPS executed'),
        ('cancelled', 'Cancelled — lost conflict resolution'),
        ('reversed',  'Reversed — EPS failed'),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash    = models.CharField(max_length=64, unique=True)
    customer      = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='pending_settlements')
    merchant      = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='pending_settlements')
    amount        = models.DecimalField(max_digits=10, decimal_places=2)
    trust_score   = models.IntegerField(default=0)
    status        = models.CharField(max_length=20, choices=STATUS, default='pending')
    received_at   = models.DateTimeField(auto_now_add=True)
    settle_after  = models.DateTimeField()
    settled_at    = models.DateTimeField(null=True, blank=True)
    conflict_note = models.TextField(blank=True)

    class Meta:
        ordering = ['-trust_score', 'received_at']

    def __str__(self):
        return f'{self.merchant.name} — E{self.amount} — {self.status}'
