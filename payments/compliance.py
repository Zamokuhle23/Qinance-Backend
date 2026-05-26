"""
Qinance — Compliance Engine (Full)
====================================
Phase 1: AML — velocity, structuring, CTR, limits, unusual hours
Phase 2: Affordability — debt-to-income ratio
Phase 3: KYC — document expiry, periodic re-KYC
Phase 4: PDPA — consent logging, right to erasure
Phase 5: PCI-DSS — CVV encryption, PAN masking/tokenisation
Phase 6: Merchant KYC — due diligence, risk rating
Phase 7: Sanctions — name screening
"""

import hashlib
from decimal import Decimal
from datetime import timedelta, date

from django.utils import timezone
from django.conf import settings

from users.models import FraudFlag, User


# ── Constants ─────────────────────────────────────────────────────────────────

CTR_THRESHOLD          = Decimal('10000.00')
VELOCITY_WINDOW_MINS   = 60
VELOCITY_MAX_TXN_COUNT = 5
VELOCITY_MAX_AMOUNT    = Decimal('5000.00')
STRUCTURING_LOW        = Decimal('8000.00')
STRUCTURING_HIGH       = Decimal('9900.00')
STRUCTURING_MIN_COUNT  = 2
REKYC_STANDARD_YEARS   = 2
REKYC_HIGH_RISK_YEARS  = 1
DTI_MAX_RATIO          = 0.40
MIN_INCOME_MULTIPLIER  = 3.0

SANCTIONS_KEYWORDS = [
    'al-qaeda', 'isis', 'isil', 'daesh', 'hamas', 'hezbollah',
    'taliban', 'boko haram', 'al shabaab', 'al shabab',
]


# ── Result object ─────────────────────────────────────────────────────────────

class ComplianceResult:

    def __init__(self):
        self.allowed      = True
        self.reason       = ''
        self.flags        = []
        self.requires_ctr = False
        self.requires_str = False
        self.risk_level   = 'low'
        self.warnings     = []

    def block(self, reason):
        self.allowed = False
        self.reason  = reason

    def add_flag(self, flag_type, description):
        self.flags.append((flag_type, description))
        self.requires_str = True

    def warn(self, message):
        self.warnings.append(message)

    def __bool__(self):
        return self.allowed


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Payment AML checks
# ══════════════════════════════════════════════════════════════════════════════

def run_payment_compliance(customer, amount, funding_mode, session):
    result = ComplianceResult()
    amount = Decimal(str(amount))

    _check_account_blocked(customer, result)
    if not result.allowed:
        return result

    if funding_mode == 'credit':
        _check_card_frozen(customer, result)
        if not result.allowed:
            return result
        _check_credit_available(customer, amount, result)
        if not result.allowed:
            return result

    _check_daily_limit(customer, amount, result)
    if not result.allowed:
        return result

    _check_monthly_limit(customer, amount, result)
    if not result.allowed:
        return result

    _check_velocity(customer, amount, result)
    _check_structuring(customer, amount, result)
    _check_ctr(amount, result)
    _check_unusual_hours(amount, result)
    _check_kyc_expiry(customer, result)

    if len(result.flags) >= 3:
        result.risk_level = 'high'
    elif len(result.flags) >= 1:
        result.risk_level = 'medium'

    return result


def _check_account_blocked(customer, result):
    try:
        u = User.objects.get(phone=customer.phone)
        if u.risk_level == 'blocked':
            result.block('Your account has been suspended. Contact support@qinance.sz.')
        elif not u.is_active:
            result.block('Your account is inactive.')
    except User.DoesNotExist:
        pass


def _check_card_frozen(customer, result):
    try:
        from payments.models import CardDetails
        card = CardDetails.objects.get(customer=customer)
        if card.status == 'frozen':
            result.block('Your Qinance card is frozen. Unfreeze it in the app first.')
        elif card.status == 'cancelled':
            result.block('Your Qinance card has been cancelled.')
    except Exception:
        pass


def _check_credit_available(customer, amount, result):
    if customer.available_credit < amount:
        result.block(
            f'Insufficient credit. Available: E{customer.available_credit:.2f}, '
            f'Required: E{amount:.2f}.'
        )


def _check_daily_limit(customer, amount, result):
    from payments.models import CreditTransaction
    from django.db.models import Sum
    today = timezone.now().date()
    used  = CreditTransaction.objects.filter(
        customer=customer, transaction_type='purchase',
        created_at__date=today,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    try:
        limit = User.objects.get(phone=customer.phone).daily_transaction_limit
    except User.DoesNotExist:
        limit = Decimal('5000.00')
    if used + amount > limit:
        result.block(
            f'Daily limit of E{limit:.2f} exceeded. '
            f'Used: E{used:.2f}. Remaining: E{max(limit - used, 0):.2f}.'
        )


def _check_monthly_limit(customer, amount, result):
    from payments.models import CreditTransaction
    from django.db.models import Sum
    month_start = timezone.now().date().replace(day=1)
    used = CreditTransaction.objects.filter(
        customer=customer, transaction_type='purchase',
        created_at__date__gte=month_start,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    try:
        limit = User.objects.get(phone=customer.phone).monthly_transaction_limit
    except User.DoesNotExist:
        limit = Decimal('50000.00')
    if used + amount > limit:
        result.block(
            f'Monthly limit of E{limit:.2f} exceeded. Used: E{used:.2f}.'
        )


def _check_velocity(customer, amount, result):
    from payments.models import CreditTransaction
    from django.db.models import Sum
    since  = timezone.now() - timedelta(minutes=VELOCITY_WINDOW_MINS)
    recent = CreditTransaction.objects.filter(
        customer=customer, transaction_type='purchase', created_at__gte=since,
    )
    count = recent.count()
    cumul = recent.aggregate(t=Sum('amount'))['t'] or Decimal('0')
    if count >= VELOCITY_MAX_TXN_COUNT:
        result.add_flag('velocity',
            f'{count} transactions in {VELOCITY_WINDOW_MINS} mins '
            f'(max {VELOCITY_MAX_TXN_COUNT}). Total: E{cumul:.2f}.')
    if cumul + amount >= VELOCITY_MAX_AMOUNT:
        result.add_flag('velocity',
            f'Cumulative E{cumul + amount:.2f} in {VELOCITY_WINDOW_MINS} mins '
            f'exceeds E{VELOCITY_MAX_AMOUNT:.2f}.')


def _check_structuring(customer, amount, result):
    from payments.models import CreditTransaction
    if STRUCTURING_LOW <= amount <= STRUCTURING_HIGH:
        today = timezone.now().date()
        count = CreditTransaction.objects.filter(
            customer=customer, transaction_type='purchase',
            created_at__date=today,
            amount__gte=STRUCTURING_LOW, amount__lte=STRUCTURING_HIGH,
        ).count()
        if count >= STRUCTURING_MIN_COUNT:
            result.add_flag('velocity',
                f'Possible structuring: {count + 1} transactions between '
                f'E{STRUCTURING_LOW}–E{STRUCTURING_HIGH} today.')
            result.requires_str = True


def _check_ctr(amount, result):
    if amount >= CTR_THRESHOLD:
        result.requires_ctr = True
        result.add_flag('velocity',
            f'Transaction of E{amount:.2f} meets CTR threshold '
            f'(E{CTR_THRESHOLD:.2f}). Report required to CBE FIU.')


def _check_unusual_hours(amount, result):
    try:
        import pytz
        hour = timezone.now().astimezone(pytz.timezone('Africa/Mbabane')).hour
        if 0 <= hour < 4:
            result.add_flag('location',
                f'Transaction of E{amount:.2f} at {hour:02d}:00 Eswatini time '
                f'(midnight–4am).')
    except Exception:
        pass


def _check_kyc_expiry(customer, result):
    try:
        u = User.objects.get(phone=customer.phone)
        if u.kyc_status != 'approved':
            return
        from users.models import AuditLog
        last_kyc = AuditLog.objects.filter(
            user=u, action='kyc_approved'
        ).order_by('-created_at').first()
        if last_kyc:
            years    = REKYC_HIGH_RISK_YEARS if u.risk_level in ('medium', 'high') \
                       else REKYC_STANDARD_YEARS
            due_date = last_kyc.created_at + timedelta(days=365 * years)
            if timezone.now() > due_date:
                result.add_flag('manual',
                    f'KYC re-verification overdue ({years}-year cycle). '
                    f'Last approved: {last_kyc.created_at.date()}.')
    except Exception:
        pass


def save_compliance_flags(customer, result):
    if not result.flags:
        return
    try:
        u = User.objects.get(phone=customer.phone)
    except User.DoesNotExist:
        return
    for flag_type, description in result.flags:
        FraudFlag.objects.create(
            user=u, flag_type=flag_type,
            description=description, status='open',
        )
        u.fraud_flags += 1
    if result.risk_level == 'high' and u.risk_level != 'blocked':
        u.risk_level = 'high'
    elif result.risk_level == 'medium' and u.risk_level == 'low':
        u.risk_level = 'medium'
    u.save()


def create_regulatory_report(customer, amount, session, report_type, flags):
    try:
        from payments.models import RegulatoryReport
        RegulatoryReport.objects.create(
            report_type=report_type,
            customer_phone=customer.phone,
            customer_name=customer.full_name,
            amount=amount,
            session_id=str(session.id),
            flag_details='\n'.join([d for _, d in flags]),
            status='pending_submission',
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Affordability engine
# ══════════════════════════════════════════════════════════════════════════════

def run_affordability_check(monthly_income, requested_limit, existing_balance=None):
    """
    Returns (approved: bool, recommended_limit: Decimal, reason: str)
    Rules:
      - Minimum income E1,000/month
      - Credit limit max = 3x monthly income
      - DTI ratio max 40% of annual income
    """
    monthly_income   = Decimal(str(monthly_income))
    requested_limit  = Decimal(str(requested_limit))
    existing_balance = Decimal(str(existing_balance or 0))

    if monthly_income < Decimal('1000.00'):
        return False, Decimal('0'), 'Minimum monthly income of E1,000 required.'

    max_by_income = monthly_income * Decimal(str(MIN_INCOME_MULTIPLIER))
    annual_income = monthly_income * 12
    total_exposure = existing_balance + requested_limit
    dti_ratio = float(total_exposure) / float(annual_income)

    if dti_ratio > DTI_MAX_RATIO:
        max_by_dti = (annual_income * Decimal(str(DTI_MAX_RATIO))) - existing_balance
        if max_by_dti <= 0:
            return (False, Decimal('0'),
                f'DTI ratio {dti_ratio * 100:.1f}% already at limit. '
                f'Existing obligations: E{existing_balance:.2f}.')
        recommended = min(max_by_income, max_by_dti).quantize(Decimal('0.01'))
        return (True, recommended,
            f'Requested E{requested_limit:.2f} exceeds DTI limit. '
            f'Recommended: E{recommended:.2f} (DTI: {dti_ratio * 100:.1f}%).')

    if requested_limit > max_by_income:
        recommended = max_by_income.quantize(Decimal('0.01'))
        return (True, recommended,
            f'Requested E{requested_limit:.2f} exceeds 3x income cap. '
            f'Recommended: E{recommended:.2f}.')

    return True, requested_limit, 'Affordability check passed.'


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — KYC document expiry & periodic re-KYC
# ══════════════════════════════════════════════════════════════════════════════

def check_kyc_document_expiry(user):
    """Returns list of (document_type, status, message) for expired/expiring docs."""
    from users.models import KYCDocument
    results = []
    today   = date.today()
    soon    = today + timedelta(days=30)
    for doc in KYCDocument.objects.filter(user=user, status='approved'):
        if not hasattr(doc, 'expiry_date') or not doc.expiry_date:
            continue
        if doc.expiry_date < today:
            results.append((doc.document_type, 'expired',
                f'{doc.get_document_type_display()} expired on {doc.expiry_date}.'))
        elif doc.expiry_date <= soon:
            results.append((doc.document_type, 'expiring_soon',
                f'{doc.get_document_type_display()} expires on {doc.expiry_date}. Renew within 30 days.'))
    return results


def flag_rekyc_due_users():
    """Batch job — flag all users whose re-KYC is overdue. Call from cron/Celery."""
    from users.models import AuditLog
    flagged = 0
    for u in User.objects.filter(kyc_status='approved'):
        last_kyc = AuditLog.objects.filter(
            user=u, action='kyc_approved'
        ).order_by('-created_at').first()
        if not last_kyc:
            continue
        years    = REKYC_HIGH_RISK_YEARS if u.risk_level in ('medium', 'high') \
                   else REKYC_STANDARD_YEARS
        due_date = last_kyc.created_at + timedelta(days=365 * years)
        if timezone.now() > due_date:
            already = FraudFlag.objects.filter(
                user=u, flag_type='manual',
                description__icontains='re-KYC', status='open',
            ).exists()
            if not already:
                FraudFlag.objects.create(
                    user=u, flag_type='manual', status='open',
                    description=(
                        f'KYC re-verification overdue. '
                        f'Last approved: {last_kyc.created_at.date()}. '
                        f'Cycle: {years} year(s).'
                    ),
                )
                flagged += 1
    return flagged


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — PDPA consent logging
# ══════════════════════════════════════════════════════════════════════════════

def log_consent(user, consent_type, request, accepted=True):
    """
    Record explicit consent with timestamp + IP.
    consent_type: terms_and_conditions | privacy_policy |
                  credit_bureau_check | marketing_communications | data_sharing
    """
    try:
        from users.models import ConsentLog
        ConsentLog.objects.create(
            user=user,
            consent_type=consent_type,
            accepted=accepted,
            ip_address=request.META.get('REMOTE_ADDR', '0.0.0.0'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except Exception:
        pass


def get_user_consents(user):
    try:
        from users.models import ConsentLog
        return ConsentLog.objects.filter(user=user).order_by('-created_at')
    except Exception:
        return []


def request_data_erasure(user, request):
    """PDPA right-to-erasure. Anonymises PII, retains transaction records."""
    import uuid
    anon        = str(uuid.uuid4())[:8]
    user.full_name  = f'DELETED_{anon}'
    user.email      = f'deleted_{anon}@deleted.qinance'
    user.national_id = ''
    user.is_active  = False
    user.save()
    log_consent(user, 'data_erasure_requested', request, accepted=True)
    return True, 'User data anonymised. Transaction records retained for regulatory compliance.'


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5 — PCI-DSS card security
# ══════════════════════════════════════════════════════════════════════════════

def mask_pan(card_number):
    """Return masked PAN — last 4 digits only. Never log full PAN."""
    if not card_number or len(card_number) < 4:
        return '****'
    return f'**** **** **** {card_number[-4:]}'


def tokenise_pan(card_number):
    """One-way PAN token for receipts/logs. Replace with vault in production."""
    salt = getattr(settings, 'SECRET_KEY', 'qinance-salt')
    return hashlib.sha256(f'{salt}{card_number}'.encode()).hexdigest()[:16]


def encrypt_cvv(cvv):
    """Hash CVV — must not be stored in plaintext. PCI-DSS 3.2.1."""
    salt = getattr(settings, 'SECRET_KEY', 'qinance-salt')
    return hashlib.sha256(f'{salt}{cvv}'.encode()).hexdigest()


def verify_cvv(raw_cvv, stored_hash):
    return encrypt_cvv(raw_cvv) == stored_hash


def sanitise_card_for_log(card_number, cvv=None):
    """Safe dict for any audit log — never exposes raw PAN or CVV."""
    return {
        'pan_masked': mask_pan(card_number),
        'pan_token':  tokenise_pan(card_number),
        'cvv':        '[REDACTED]',
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 6 — Merchant KYC / due diligence
# ══════════════════════════════════════════════════════════════════════════════

def run_merchant_due_diligence(merchant):
    """
    Run compliance checks before activating a merchant.
    Returns (approved: bool, issues: list of str)
    """
    issues = []

    # Sanctions check
    for keyword in SANCTIONS_KEYWORDS:
        if keyword in merchant.name.lower():
            return False, [f'Merchant name matches sanctions list: {keyword}']

    # Required documents
    try:
        from payments.models import MerchantDocument
        doc_types = set(
            MerchantDocument.objects.filter(merchant=merchant)
            .values_list('document_type', flat=True)
        )
        missing = {'business_registration', 'tax_clearance', 'bank_letter'} - doc_types
        if missing:
            issues.append(f'Missing documents: {", ".join(missing)}')
    except Exception:
        issues.append('Could not verify merchant documents.')

    # High-risk business type
    HIGH_RISK = ['money transfer', 'forex', 'gambling', 'crypto', 'pawn',
                 'second hand goods', 'tobacco', 'alcohol']
    btype = (merchant.business_type or '').lower()
    if any(r in btype for r in HIGH_RISK):
        issues.append(
            f'High-risk business type: {merchant.business_type}. '
            f'Enhanced due diligence required.'
        )

    return len(issues) == 0, issues


def calculate_merchant_risk_rating(merchant):
    """Return low / medium / high risk rating for a merchant."""
    from payments.models import PaymentSession
    from django.db.models import Sum

    score = 0
    HIGH_RISK = ['money transfer', 'forex', 'gambling', 'crypto', 'pawn']
    if any(r in (merchant.business_type or '').lower() for r in HIGH_RISK):
        score += 3

    month_ago   = timezone.now() - timedelta(days=30)
    monthly_vol = PaymentSession.objects.filter(
        merchant=merchant, status='confirmed', created_at__gte=month_ago,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    if monthly_vol > Decimal('500000'):
        score += 2
    elif monthly_vol > Decimal('100000'):
        score += 1

    return 'high' if score >= 3 else 'medium' if score >= 1 else 'low'


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 7 — Sanctions screening
# ══════════════════════════════════════════════════════════════════════════════

def run_registration_compliance(full_name):
    """Screen name at registration. Returns (allowed: bool, reason: str)."""
    name_lower = full_name.lower()
    for keyword in SANCTIONS_KEYWORDS:
        if keyword in name_lower:
            return False, (
                f'Registration blocked: name matches sanctions screening list. '
                f'Contact support@qinance.sz if this is an error.'
            )
    return True, ''


def screen_existing_customers():
    """Monthly batch re-screening of all active customers."""
    from payments.models import Customer
    flagged = 0
    for c in Customer.objects.filter(is_active=True):
        allowed, reason = run_registration_compliance(c.full_name)
        if not allowed:
            try:
                u = User.objects.get(phone=c.phone)
                FraudFlag.objects.get_or_create(
                    user=u, flag_type='manual', status='open',
                    defaults={'description': f'Sanctions re-screening match: {reason}'},
                )
                u.risk_level = 'blocked'
                u.save()
                flagged += 1
            except User.DoesNotExist:
                pass
    return flagged
