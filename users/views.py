from django.conf import settings

from payments.compliance import (
    run_registration_compliance,
    run_affordability_check,
    log_consent,
    request_data_erasure,
    check_kyc_document_expiry,
)
from django.core.mail import send_mail
from django.http import FileResponse
import mimetypes
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

from .models import (
    User,
    KYCDocument,
    OTPVerification,
    CreditApplication,
    FraudFlag,
    FCMDevice,
)

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    SetPinSerializer,
    PinLoginSerializer,
    KYCDocumentSerializer,
    CreditApplicationSerializer,
    RejectSerializer,
    build_auth_payload,
)

from .permissions import (
    IsKYCOfficer,
    IsCreditOfficer,
    IsFraudAnalyst,
    IsSuperAdmin,
)

from .utils import (
    register_device,
    create_audit_log,
)


# ── Email helper ──────────────────────────────────────────────────────────────

def send_otp_email(user, otp_code):
    """
    Send OTP to user's email address.
    Uses Gmail SMTP when EMAIL_BACKEND is set in .env.
    Silently skips if user has no email.
    """
    if not user.email:
        return False

    sent = send_mail(
        subject='Your Qinance verification code',
        message=(
            f'Hi {user.full_name},\n\n'
            f'Your Qinance verification code is:\n\n'
            f'        {otp_code}\n\n'
            f'This code expires in 5 minutes.\n\n'
            f'If you did not request this, please ignore this email.\n\n'
            f'— The Qinance Team'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    return sent == 1


# ── Registration ──────────────────────────────────────────────────────────────

def _delete_unfinished_registration(user):
    from payments.models import Customer, Merchant

    Merchant.objects.filter(phone=user.phone).delete()
    Customer.objects.filter(phone=user.phone).delete()
    user.delete()


def _resume_registration(request, user, password):
    if not user.check_password(password):
        return Response({'error': 'An unfinished application already uses these details. Enter the same password to continue.'}, status=409)
    if user.kyc_status == 'approved':
        return Response({'error': 'This account is already approved. Sign in instead.', 'resume_stage': 'approved'}, status=409)
    if user.kyc_status == 'under_review':
        return Response({'message': 'Your application is awaiting administrator review.', 'resume_stage': 'pending_review'})
    if user.kyc_status == 'rejected':
        return Response({'error': user.rejection_reason or 'This application was rejected. Contact support.', 'resume_stage': 'rejected'}, status=409)

    retention_days = int(getattr(settings, 'PENDING_REGISTRATION_RETENTION_DAYS', 7))
    user.registration_expires_at = timezone.now() + timedelta(days=retention_days)
    user.save(update_fields=['registration_expires_at'])

    if not user.is_phone_verified:
        OTPVerification.objects.filter(user=user, purpose='phone_verification', is_used=False).update(is_used=True)
        otp = OTPVerification.generate_otp(user, 'phone_verification')
        send_otp_email(user, otp.code)
        response = {
            'message': 'Application found. A new verification code was sent to your email.',
            'resume_stage': 'email_verification',
            'user_id': str(user.id),
        }
        if settings.DEBUG:
            response['otp_code'] = otp.code
        return Response(response)

    response = build_auth_payload(user)
    response.update({
        'message': 'Application found. Continue your identity verification.',
        'resume_stage': 'identity_verification',
    })
    return Response(response)


class RegisterView(APIView):
    def post(self, request):

        email = str(request.data.get('email', '')).strip().lower()
        phone = str(request.data.get('phone', '')).strip()
        existing = list(User.objects.filter(Q(email__iexact=email) | Q(phone=phone)).distinct())
        if existing:
            if len(existing) != 1 or existing[0].email.lower() != email or existing[0].phone != phone:
                return Response({'error': 'That email or phone number belongs to another application.'}, status=409)
            user = existing[0]
            if user.registration_expires_at and user.registration_expires_at <= timezone.now():
                _delete_unfinished_registration(user)
            else:
                return _resume_registration(request, user, request.data.get('password', ''))

        serializer = RegisterSerializer(data=request.data)

        if serializer.is_valid():

            # Sanctions screening before creating account
            full_name = serializer.validated_data.get('full_name', '')
            allowed, reason = run_registration_compliance(full_name)
            if not allowed:
                return Response({'error': reason}, status=400)

            try:
                validate_password(serializer.validated_data.get('password'))
            except DjangoValidationError as error:
                return Response({'error': list(error.messages)}, status=400)

            with transaction.atomic():
                retention_days = int(getattr(settings, 'PENDING_REGISTRATION_RETENTION_DAYS', 7))
                user = serializer.save(registration_expires_at=timezone.now() + timedelta(days=retention_days))

            otp = OTPVerification.generate_otp(
                user,
                'phone_verification'
            )

            send_otp_email(user, otp.code)

            create_audit_log(
                user=user,
                action='otp_verified',
                request=request,
            )

            # Log PDPA consents accepted at registration
            log_consent(user, 'terms_and_conditions', request)
            log_consent(user, 'privacy_policy', request)

            response_data = {
                "message": (
                    "Registration successful. Check your email for the OTP."
                    if user.email
                    else "Registration successful."
                ),
                "user_id": str(user.id),
            }

            # Only expose OTP in response when DEBUG=True (local dev)
            if settings.DEBUG:
                response_data["otp_code"] = otp.code

            return Response(response_data, status=201)

        return Response(serializer.errors, status=400)


# ── Resend OTP ────────────────────────────────────────────────────────────────

class ResendOTPView(APIView):

    def post(self, request):

        phone = request.data.get('phone')

        if not phone:
            return Response(
                {"error": "phone is required"},
                status=400
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        if user.is_phone_verified:
            return Response(
                {"error": "Phone is already verified"},
                status=400
            )

        otp = OTPVerification.generate_otp(user, 'phone_verification')

        send_otp_email(user, otp.code)

        response_data = {
            "message": (
                "OTP resent. Check your email."
                if user.email
                else "OTP resent."
            )
        }

        if settings.DEBUG:
            response_data["otp_code"] = otp.code

        return Response(response_data)


# ── Phone OTP Verification ────────────────────────────────────────────────────

class VerifyPhoneOTPView(APIView):

    def post(self, request):

        phone = request.data.get('phone')
        code = request.data.get('code')

        if not phone or not code:
            return Response(
                {"error": "phone and code are required"},
                status=400
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        success, message = OTPVerification.verify(
            user, code, 'phone_verification'
        )

        if not success:
            return Response({"error": message}, status=400)

        user.is_phone_verified = True
        user.save()

        create_audit_log(
            user=user,
            action='otp_verified',
            request=request,
        )

        response = build_auth_payload(user)
        response["message"] = "Email verified successfully"
        return Response(response)


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginView(APIView):

    def post(self, request):

        serializer = LoginSerializer(data=request.data)

        if serializer.is_valid():

            user = User.objects.get(id=serializer.validated_data['user_id'])

            register_device(request, user)

            create_audit_log(
                user=user,
                action='login',
                request=request,
            )

            return Response(serializer.validated_data)

        return Response(serializer.errors, status=400)


def _authenticate_identifier(identifier, password):
    try:
        account = User.objects.get(
            Q(email__iexact=identifier) | Q(phone__iexact=identifier)
        )
    except (User.DoesNotExist, User.MultipleObjectsReturned):
        return None
    return authenticate(username=account.phone, password=password)


class WebLoginStartView(APIView):
    """Verify the password, then email a fresh OTP for every browser login."""

    def post(self, request):
        identifier = str(request.data.get('identifier', '')).strip()
        password = request.data.get('password', '')
        user = _authenticate_identifier(identifier, password)
        if not user or not user.is_active:
            return Response({'error': 'Invalid credentials'}, status=400)
        if not (user.is_staff or user.is_superuser) and user.kyc_status != 'approved':
            return Response({'error': 'Your application is still awaiting administrator approval.'}, status=403)
        if not user.email:
            return Response({'error': 'This account has no email address. Contact an administrator.'}, status=400)

        OTPVerification.objects.filter(user=user, purpose='web_login', is_used=False).update(is_used=True)
        otp = OTPVerification.generate_otp(user, 'web_login')
        if not send_otp_email(user, otp.code):
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            return Response({'error': 'Could not send the sign-in email. Try again later.'}, status=503)
        response = {
            'message': 'A sign-in code was sent to your email.',
            'email_hint': _mask_email(user.email),
        }
        if settings.DEBUG:
            response['otp_code'] = otp.code
        return Response(response)


class WebLoginVerifyView(APIView):
    def post(self, request):
        identifier = str(request.data.get('identifier', '')).strip()
        code = str(request.data.get('code', '')).strip()
        try:
            user = User.objects.get(Q(email__iexact=identifier) | Q(phone__iexact=identifier))
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            return Response({'error': 'Invalid or expired code'}, status=400)

        success, message = OTPVerification.verify(user, code, 'web_login')
        if not success:
            return Response({'error': message}, status=400)
        if not (user.is_staff or user.is_superuser) and user.kyc_status != 'approved':
            return Response({'error': 'Your application is still awaiting administrator approval.'}, status=403)
        register_device(request, user)
        create_audit_log(user=user, action='login', request=request)
        return Response(build_auth_payload(user))


def _mask_email(email):
    local, domain = email.split('@', 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f'{visible}{"*" * max(3, len(local) - len(visible))}@{domain}'


class AdminAccountListCreateView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        accounts = User.objects.order_by('-created_at')
        return Response([{
            'id': str(user.id),
            'full_name': user.full_name,
            'email': user.email,
            'phone': user.phone,
            'role': build_auth_payload_without_tokens(user)['role'],
            'is_active': user.is_active,
            'kyc_status': user.kyc_status,
            'created_at': user.created_at,
        } for user in accounts])

    def post(self, request):
        from payments.models import CardDetails, Customer, Merchant

        account_type = request.data.get('account_type')
        email = str(request.data.get('email', '')).strip().lower()
        phone = str(request.data.get('phone', '')).strip()
        full_name = str(request.data.get('full_name', '')).strip()
        password = request.data.get('password', '')
        if account_type not in ('merchant', 'customer'):
            return Response({'error': 'account_type must be merchant or customer'}, status=400)
        if not all([email, phone, full_name, password]):
            return Response({'error': 'email, phone, full_name, and password are required'}, status=400)
        if User.objects.filter(Q(email__iexact=email) | Q(phone=phone)).exists():
            return Response({'error': 'An account already uses that email or phone'}, status=400)
        try:
            validate_password(password)
        except DjangoValidationError as error:
            return Response({'error': list(error.messages)}, status=400)

        with transaction.atomic():
            user = User.objects.create_user(
                phone=phone,
                email=email,
                full_name=full_name,
                password=password,
                role=account_type,
                national_id=str(request.data.get('national_id', '')).strip(),
                kyc_status='approved' if request.data.get('kyc_approved', True) else 'pending',
                is_phone_verified=True,
            )
            if account_type == 'merchant':
                profile = Merchant.objects.create(
                    name=full_name,
                    phone=phone,
                    business_type=str(request.data.get('business_type', '')).strip(),
                    location=str(request.data.get('location', '')).strip(),
                    is_active=True,
                    kyc_approved=bool(request.data.get('kyc_approved', True)),
                )
                profile_id = profile.id
            else:
                profile = Customer.objects.create(
                    phone=phone,
                    full_name=full_name,
                    national_id=user.national_id,
                    bank=request.data.get('bank', ''),
                    credit_limit=request.data.get('credit_limit') or 0,
                )
                CardDetails.objects.create(customer=profile)
                profile_id = profile.id

        return Response({
            'message': f'{account_type.title()} account created',
            'user_id': str(user.id),
            'profile_id': str(profile_id),
        }, status=201)


def build_auth_payload_without_tokens(user):
    from .serializers import resolve_account
    role, _ = resolve_account(user)
    return {'role': role}


# ── PIN ───────────────────────────────────────────────────────────────────────

class SetPinView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        serializer = SetPinSerializer(data=request.data)

        if serializer.is_valid():

            request.user.set_pin(serializer.validated_data['pin'])
            request.user.save()

            return Response({"message": "PIN set successfully"})

        return Response(serializer.errors, status=400)


class PinLoginView(APIView):

    def post(self, request):

        serializer = PinLoginSerializer(data=request.data)

        if serializer.is_valid():

            phone = request.data.get('phone')
            user = User.objects.get(phone=phone)

            register_device(request, user)

            create_audit_log(
                user=user,
                action='pin_login',
                request=request,
            )

            return Response(serializer.validated_data)

        return Response(serializer.errors, status=400)


# ── KYC ───────────────────────────────────────────────────────────────────────

class UploadKYCDocumentView(APIView):

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):

        if not request.user.consents.filter(
            consent_type='biometric_identity_verification', accepted=True
        ).exists():
            consent = str(request.data.get('biometric_consent', '')).lower()
            if consent not in ('true', '1', 'yes'):
                return Response({'error': 'Explicit consent is required for guided face verification.'}, status=400)
            log_consent(request.user, 'biometric_identity_verification', request)

        serializer = KYCDocumentSerializer(data=request.data)

        if serializer.is_valid():

            document_type = serializer.validated_data['document_type']
            allowed_types = ('id', 'selfie_front', 'selfie_left', 'selfie_right')
            if document_type not in allowed_types:
                return Response({'error': 'Upload the ID and guided centre, left, and right selfie captures.'}, status=400)

            KYCDocument.objects.filter(
                user=request.user,
                document_type=document_type,
            ).delete()

            serializer.save(user=request.user)

            uploaded = set(request.user.documents.values_list('document_type', flat=True))
            required = {'id', 'selfie_front', 'selfie_left', 'selfie_right'}
            request.user.kyc_status = 'under_review' if required <= uploaded else 'pending'
            if request.user.kyc_status == 'under_review':
                request.user.registration_expires_at = None
            elif request.user.registration_expires_at:
                retention_days = int(getattr(settings, 'PENDING_REGISTRATION_RETENTION_DAYS', 7))
                request.user.registration_expires_at = timezone.now() + timedelta(days=retention_days)
            request.user.save()

            verification = None
            if request.user.kyc_status == 'under_review':
                from .kyc_vision import evaluate_user_kyc
                verification = evaluate_user_kyc(request.user)

            create_audit_log(
                user=request.user,
                action='kyc_upload',
                request=request,
            )

            return Response({
                "message": "Application submitted for review" if request.user.kyc_status == 'under_review' else "Document uploaded successfully",
                "kyc_status": request.user.kyc_status,
                "uploaded": sorted(uploaded),
                "recommendation": verification.recommendation if verification else None,
            })

        return Response(serializer.errors, status=400)


class PendingKYCApplicationsView(APIView):
    permission_classes = [IsKYCOfficer]

    def get(self, request):
        users = User.objects.filter(
            role__in=('customer', 'merchant'),
            kyc_status='under_review',
        ).prefetch_related('documents').order_by('created_at')

        return Response([{
            'user_id': str(user.id),
            'account_type': user.role,
            'full_name': user.full_name,
            'email': user.email,
            'phone': user.phone,
            'national_id': user.national_id,
            'created_at': user.created_at,
            'documents': [{
                'id': str(document.id),
                'document_type': document.document_type,
                'url': f'/api/auth/admin/kyc/documents/{document.id}/',
                'uploaded_at': document.uploaded_at,
            } for document in user.documents.all()],
            'verification': ({
                'similarity_score': user.kyc_verification.similarity_score,
                'pose_challenge_passed': user.kyc_verification.pose_challenge_passed,
                'recommendation': user.kyc_verification.recommendation,
                'details': user.kyc_verification.details,
            } if hasattr(user, 'kyc_verification') else None),
        } for user in users])


class KYCApplicationDocumentView(APIView):
    permission_classes = [IsKYCOfficer]

    def get(self, request, document_id):
        try:
            document = KYCDocument.objects.get(id=document_id)
        except KYCDocument.DoesNotExist:
            return Response({'error': 'Document not found'}, status=404)
        return FileResponse(
            document.file.open('rb'),
            content_type=mimetypes.guess_type(document.file.name)[0] or 'application/octet-stream',
            as_attachment=False,
            filename=document.file.name.rsplit('/', 1)[-1],
        )


class ApproveKYCView(APIView):

    permission_classes = [IsKYCOfficer]

    def post(self, request, user_id):

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        uploaded = set(user.documents.values_list('document_type', flat=True))
        required = {'id', 'selfie_front', 'selfie_left', 'selfie_right'}
        if not required <= uploaded:
            return Response({'error': 'ID plus centre, left, and right guided selfies are required before approval.'}, status=400)

        from payments.models import CardDetails, Customer, Merchant

        with transaction.atomic():
            user.kyc_status = 'approved'
            user.rejection_reason = ''
            user.save(update_fields=['kyc_status', 'rejection_reason'])
            user.documents.update(status='approved', rejection_reason='')
            if user.role == 'merchant':
                Merchant.objects.filter(phone=user.phone).update(is_active=True, kyc_approved=True)
            elif user.role == 'customer':
                customer = Customer.objects.get(phone=user.phone)
                customer.is_active = True
                customer.save(update_fields=['is_active'])
                CardDetails.objects.get_or_create(customer=customer)

        if user.email:
            send_mail(
                subject='Your Qinance KYC has been approved',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Great news! Your {user.role} account has been approved.\n'
                    f'You can now sign in to Qinance using the password you created.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='kyc_approved',
            request=request,
            metadata={"approved_user": str(user.id)}
        )

        return Response({"message": "KYC approved"})


class RejectKYCView(APIView):

    permission_classes = [IsKYCOfficer]

    def post(self, request, user_id):

        serializer = RejectSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        user.kyc_status = 'rejected'
        user.rejection_reason = serializer.validated_data['reason']
        user.save()
        user.documents.update(status='rejected', rejection_reason=serializer.validated_data['reason'])

        from payments.models import Customer, Merchant
        Merchant.objects.filter(phone=user.phone).update(is_active=False, kyc_approved=False)
        Customer.objects.filter(phone=user.phone).update(is_active=False)

        if user.email:
            send_mail(
                subject='Qinance — Action required on your application',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Unfortunately we could not verify your identity.\n\n'
                    f'Reason: {serializer.validated_data["reason"]}\n\n'
                    f'Please re-upload your documents or contact us at '
                    f'support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='kyc_rejected',
            request=request,
            metadata={"rejected_user": str(user.id)}
        )

        return Response({"message": "KYC rejected"})


# ── Credit Application ────────────────────────────────────────────────────────

class SubmitCreditApplicationView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        if request.user.kyc_status != 'approved':
            return Response(
                {"error": "KYC must be approved before applying for credit"},
                status=400
            )

        if hasattr(request.user, 'credit_application'):
            return Response(
                {"error": "A credit application already exists"},
                status=400
            )

        serializer = CreditApplicationSerializer(data=request.data)

        if serializer.is_valid():

            serializer.save(user=request.user, status='pending')

            request.user.credit_status = 'pending'
            request.user.save()

            return Response({
                "message": "Credit application submitted successfully"
            })

        return Response(serializer.errors, status=400)


class PendingCreditApplicationsView(APIView):

    permission_classes = [IsCreditOfficer]

    def get(self, request):

        applications = CreditApplication.objects.filter(
            status='pending'
        ).select_related('user').order_by('-created_at')

        data = [
            {
                "application_id": str(app.id),
                "user_id": str(app.user.id),
                "full_name": app.user.full_name,
                "phone": app.user.phone,
                "employer_name": app.employer_name,
                "monthly_income": str(app.monthly_income),
                "requested_limit": str(app.requested_limit),
                "created_at": app.created_at.isoformat(),
            }
            for app in applications
        ]

        return Response(data)


class ApproveCreditApplicationView(APIView):

    permission_classes = [IsCreditOfficer]

    def post(self, request, application_id):

        try:
            application = CreditApplication.objects.select_related('user').get(
                id=application_id
            )
        except CreditApplication.DoesNotExist:
            return Response({"error": "Application not found"}, status=404)

        approved_limit = request.data.get('approved_limit')

        if not approved_limit:
            return Response(
                {"error": "approved_limit is required"},
                status=400
            )

        try:
            approved_limit = float(approved_limit)
        except (ValueError, TypeError):
            return Response(
                {"error": "approved_limit must be a number"},
                status=400
            )

        # Run affordability check
        approved_limit_decimal = float(approved_limit)
        existing = float(application.user.credit_application.approved_limit or 0)                    if hasattr(application.user, 'credit_application') else 0

        aff_ok, recommended, aff_reason = run_affordability_check(
            monthly_income=application.monthly_income,
            requested_limit=approved_limit_decimal,
            existing_balance=existing,
        )

        # If affordability fails entirely, block approval
        if not aff_ok:
            return Response({'error': f'Affordability check failed: {aff_reason}'}, status=400)

        # Use recommended limit if lower than requested
        final_limit = float(recommended)

        application.status = 'approved'
        application.approved_limit = final_limit
        application.reviewed_by = request.user
        application.save()

        user = application.user
        user.credit_status = 'approved'
        user.save()

        # Log credit bureau consent
        log_consent(user, 'credit_bureau_check', request)

        # Add affordability note to response if limit was adjusted
        affordability_note = aff_reason if final_limit < approved_limit_decimal else None

        if user.email:
            send_mail(
                subject='🎉 Your Qinance credit card is approved!',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Congratulations! Your Qinance credit card has been approved.\n\n'
                    f'Approved credit limit: E{approved_limit:,.2f}\n\n'
                    f'Sign in to your Qinance account to view your virtual card '
                    f'and start shopping.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='credit_approved',
            request=request,
            metadata={
                "application_id": str(application.id),
                "approved_user": str(user.id),
                "approved_limit": approved_limit,
            }
        )

        resp = {"message": "Credit application approved", "approved_limit": final_limit}
        if affordability_note:
            resp["note"] = affordability_note
        return Response(resp)


class RejectCreditApplicationView(APIView):

    permission_classes = [IsCreditOfficer]

    def post(self, request, application_id):

        serializer = RejectSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        try:
            application = CreditApplication.objects.select_related('user').get(
                id=application_id
            )
        except CreditApplication.DoesNotExist:
            return Response({"error": "Application not found"}, status=404)

        application.status = 'rejected'
        application.rejection_reason = serializer.validated_data['reason']
        application.reviewed_by = request.user
        application.save()

        user = application.user
        user.credit_status = 'rejected'
        user.rejection_reason = serializer.validated_data['reason']
        user.save()

        if user.email:
            send_mail(
                subject='Qinance — Credit application update',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Unfortunately your credit card application was not approved '
                    f'at this time.\n\n'
                    f'Reason: {serializer.validated_data["reason"]}\n\n'
                    f'You may reapply after 90 days or contact us at '
                    f'support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='credit_rejected',
            request=request,
            metadata={
                "application_id": str(application.id),
                "rejected_user": str(user.id),
            }
        )

        return Response({"message": "Credit application rejected"})


# ── Fraud Flags ───────────────────────────────────────────────────────────────

class FraudFlagsView(APIView):

    permission_classes = [IsFraudAnalyst]

    def get(self, request):

        flags = FraudFlag.objects.filter(
            status='open'
        ).select_related('user').order_by('-created_at')

        data = [
            {
                "flag_id": str(flag.id),
                "user_id": str(flag.user.id),
                "full_name": flag.user.full_name,
                "phone": flag.user.phone,
                "risk_level": flag.user.risk_level,
                "flag_type": flag.flag_type,
                "description": flag.description,
                "status": flag.status,
                "created_at": flag.created_at.isoformat(),
            }
            for flag in flags
        ]

        return Response(data)

    def post(self, request):
        """Resolve a fraud flag"""

        flag_id = request.data.get('flag_id')

        if not flag_id:
            return Response({"error": "flag_id is required"}, status=400)

        try:
            flag = FraudFlag.objects.get(id=flag_id)
        except FraudFlag.DoesNotExist:
            return Response({"error": "Flag not found"}, status=404)

        flag.status = 'resolved'
        flag.save()

        return Response({"message": "Flag resolved"})


# ── PDPA — Data erasure & consents ────────────────────────────────────────────

class UserConsentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        consents = request.user.consents.all()
        data = [
            {
                'consent_type': c.consent_type,
                'accepted': c.accepted,
                'ip_address': c.ip_address,
                'created_at': c.created_at.isoformat(),
            }
            for c in consents
        ]
        return Response(data)


class DataErasureView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        success, message = request_data_erasure(request.user, request)
        return Response({'message': message})


# ── KYC document expiry ───────────────────────────────────────────────────────

class KYCExpiryView(APIView):
    permission_classes = [IsKYCOfficer]

    def get(self, request):
        """List all users with expired or expiring KYC documents."""
        from users.models import User as AuthUser
        results = []
        for u in AuthUser.objects.filter(kyc_status='approved'):
            issues = check_kyc_document_expiry(u)
            if issues:
                results.append({
                    'user_id': str(u.id),
                    'full_name': u.full_name,
                    'phone': u.phone,
                    'issues': [
                        {'document_type': t, 'status': s, 'message': m}
                        for t, s, m in issues
                    ],
                })
        return Response(results)


# ── Forgot / Reset Password ───────────────────────────────────────────────────

class ForgotPasswordView(APIView):
    def post(self, request):
        identifier = str(request.data.get('identifier') or request.data.get('phone') or '').strip()
        if not identifier:
            return Response({'error': 'email or phone is required'}, status=400)
        try:
            user = User.objects.get(Q(email__iexact=identifier) | Q(phone=identifier))
            OTPVerification.objects.filter(user=user, purpose='password_reset', is_used=False).update(is_used=True)
            otp = OTPVerification.generate_otp(user, 'password_reset')
            send_otp_email(user, otp.code)
            response_data = {'message': 'A reset code has been sent to your email.'}
            if settings.DEBUG:
                response_data['otp_code'] = otp.code
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            # Same message either way — don't reveal if account exists
            response_data = {'message': 'A reset code has been sent to your email.'}
        return Response(response_data)


class ResetPasswordView(APIView):
    def post(self, request):
        identifier  = str(request.data.get('identifier') or request.data.get('phone') or '').strip()
        code        = request.data.get('code')
        new_password = request.data.get('new_password')
        if not all([identifier, code, new_password]):
            return Response({'error': 'email or phone, code, and new_password are required'}, status=400)
        try:
            user = User.objects.get(Q(email__iexact=identifier) | Q(phone=identifier))
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            return Response({'error': 'Invalid request'}, status=400)
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as error:
            return Response({'error': list(error.messages)}, status=400)
        success, message = OTPVerification.verify(user, code, 'password_reset')
        if not success:
            return Response({'error': message}, status=400)
        user.set_password(new_password)
        user.is_phone_verified = True
        if user.registration_expires_at:
            retention_days = int(getattr(settings, 'PENDING_REGISTRATION_RETENTION_DAYS', 7))
            user.registration_expires_at = timezone.now() + timedelta(days=retention_days)
        user.save()
        if user.email:
            send_mail(
                subject='Qinance — Your password has been reset',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Your Qinance account password was successfully reset.\n\n'
                    f'If you did not make this change, contact us immediately at support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        if user.kyc_status == 'under_review':
            return Response({'message': 'Password reset successfully.', 'resume_stage': 'pending_review'})
        if user.kyc_status == 'rejected':
            return Response({'message': 'Password reset successfully.', 'resume_stage': 'rejected', 'error': user.rejection_reason})
        if user.kyc_status == 'approved':
            return Response({'message': 'Password reset successfully. Sign in with your new password.', 'resume_stage': 'login'})
        response = build_auth_payload(user)
        response.update({'message': 'Password reset successfully. Continue your application.', 'resume_stage': 'identity_verification'})
        return Response(response)


# ── Forgot / Reset PIN ────────────────────────────────────────────────────────

class ForgotPinView(APIView):
    def post(self, request):
        phone = request.data.get('phone')
        if not phone:
            return Response({'error': 'phone is required'}, status=400)
        try:
            user = User.objects.get(phone=phone)
            otp = OTPVerification.generate_otp(user, 'pin_reset')
            send_otp_email(user, otp.code)
            response_data = {'message': 'A PIN reset code has been sent to your email.'}
            if settings.DEBUG:
                response_data['otp_code'] = otp.code
        except User.DoesNotExist:
            response_data = {'message': 'A PIN reset code has been sent to your email.'}
        return Response(response_data)


class ResetPinView(APIView):
    def post(self, request):
        phone   = request.data.get('phone')
        code    = request.data.get('code')
        new_pin = request.data.get('new_pin')
        if not all([phone, code, new_pin]):
            return Response({'error': 'phone, code, and new_pin are required'}, status=400)
        if not str(new_pin).isdigit() or len(str(new_pin)) != 4:
            return Response({'error': 'PIN must be exactly 4 digits'}, status=400)
        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({'error': 'Invalid request'}, status=400)
        success, message = OTPVerification.verify(user, code, 'pin_reset')
        if not success:
            return Response({'error': message}, status=400)
        user.set_pin(str(new_pin))
        user.save()
        return Response({'message': 'PIN reset successfully.'})


# ── FCM Device Token ──────────────────────────────────────────────────────────

class RegisterFCMTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token     = request.data.get('token')
        device_id = request.data.get('device_id', '')
        if not token:
            return Response({'error': 'token is required'}, status=400)
        FCMDevice.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'device_id': device_id},
        )
        return Response({'message': 'FCM token registered'})
