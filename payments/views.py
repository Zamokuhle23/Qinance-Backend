from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from decimal import Decimal
from datetime import timedelta
from django.db import transaction
from django.db.models import Max

from .compliance import (
    run_payment_compliance,
    save_compliance_flags,
    create_regulatory_report,
)

from .models import (
    Merchant, Customer, CardDetails, CreditStatement,
    CreditTransaction, PaymentSession, DebitMandate,
    AgentTransaction, MerchantAgentProfile, AgentSession,
    CustomerDeviceSecret, PendingSettlement,
    LinkedMoMoAccount, MoMoTransaction, LinkedAccount, WalletEntry, MerchantLoan,
)
from .routing import (
    RoutingError, apply_payment_routes, credit_is_available, get_wallet,
    post_wallet_entry, resolve_customer_source, resolve_merchant_destination,
    routing_snapshot,
)
from . import momo as momo_client
from .serializers import (
    MerchantSerializer, CustomerSerializer, CardDetailsFullSerializer,
    CreditStatementSerializer, CreditTransactionSerializer,
    PaymentSessionSerializer, DebitMandateSerializer, MerchantLoanSerializer,
    CreateSessionSerializer, ConfirmPaymentSerializer,
    RepaymentSerializer, FreezeCardSerializer,
)


# ── FCM Push ─────────────────────────────────────────────────────────────────

def send_payment_push(user, merchant_name, amount):
    """
    Send FCM push notification to all registered devices for this user.
    Silently skips if firebase-admin is not installed or FCM_CREDENTIALS_FILE is unset.
    """
    try:
        from django.conf import settings as _s
        from users.models import FCMDevice
        import firebase_admin
        from firebase_admin import credentials, messaging

        tokens = list(FCMDevice.objects.filter(user=user).values_list('token', flat=True))
        if not tokens:
            return

        cred_path = getattr(_s, 'FCM_CREDENTIALS_FILE', '')
        if not cred_path:
            return

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(cred_path))

        messaging.send_each_for_multicast(
            messaging.MulticastMessage(
                notification=messaging.Notification(
                    title='Payment Confirmed ✅',
                    body=f'E{float(amount):.2f} paid to {merchant_name}',
                ),
                tokens=tokens,
            )
        )
    except Exception:
        pass  # Never let push failure break the payment flow


# ── Helpers ───────────────────────────────────────────────────────────────────

def session_response(session, merchant=None):
    m = merchant or session.merchant
    return {
        'session_id': str(session.id),
        'merchant_id': str(m.id),
        'merchant_name': m.name,
        'merchant_location': m.location,
        'amount': str(session.amount),
        'status': session.status,
        'funding_mode': session.funding_mode,
        'payment_source': session.payment_source,
        'settlement_destination': session.settlement_destination,
        'settlement_account_id': str(session.settlement_account_id) if session.settlement_account_id else None,
        'qr_url': f'/m/{m.id}/{session.id}',
    }


def get_or_create_card(customer):
    card, _ = CardDetails.objects.get_or_create(customer=customer)
    return card


def get_open_statement(customer):
    today = timezone.now().date()
    stmt = CreditStatement.objects.filter(
        customer=customer, status='open'
    ).first()
    if not stmt:
        period_start = today.replace(day=1)
        if today.month == 12:
            period_end = today.replace(year=today.year+1, month=1, day=1) - timedelta(days=1)
        else:
            period_end = today.replace(month=today.month+1, day=1) - timedelta(days=1)
        due_date = period_end + timedelta(days=15)
        stmt = CreditStatement.objects.create(
            customer=customer,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            opening_balance=customer.current_balance,
        )
    return stmt


# ── Merchants ─────────────────────────────────────────────────────────────────

class MerchantListView(APIView):
    def get(self, request):
        merchants = Merchant.objects.filter(is_active=True)
        return Response(MerchantSerializer(merchants, many=True).data)

    def post(self, request):
        if not request.user.is_authenticated or not (
            request.user.is_superuser or request.user.role == 'super_admin'
        ):
            return Response({'error': 'Only an administrator can create merchant accounts'}, status=403)
        serializer = MerchantSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MerchantDetailView(APIView):
    def get(self, request, pk):
        merchant = get_object_or_404(Merchant, pk=pk)
        return Response(MerchantSerializer(merchant).data)


class MerchantDueDiligenceView(APIView):
    """
    Run due diligence checks on a merchant before activation.
    Admin calls this when reviewing a new merchant signup.
    """

    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

        from .compliance import run_merchant_due_diligence
        approved, issues = run_merchant_due_diligence(merchant)

        return Response({
            'merchant_id':   str(merchant.id),
            'merchant_name': merchant.name,
            'approved':      approved,
            'issues':        issues,
        })

    def post(self, request, merchant_id):
        """Activate merchant if due diligence passes, or force-activate with override."""
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

        from .compliance import run_merchant_due_diligence
        approved, issues = run_merchant_due_diligence(merchant)
        override = request.data.get('override', False)

        if not approved and not override:
            return Response({
                'error':  'Due diligence failed',
                'issues': issues,
            }, status=400)

        merchant.is_active    = True
        merchant.kyc_approved = True
        merchant.save()

        return Response({
            'message':  'Merchant activated',
            'override': override and not approved,
        })


# ── Customers ─────────────────────────────────────────────────────────────────

class CustomerListView(APIView):
    def get(self, request):
        customers = Customer.objects.filter(is_active=True)
        return Response(CustomerSerializer(customers, many=True).data)

    def post(self, request):
        if not request.user.is_authenticated or not (
            request.user.is_superuser or request.user.role == 'super_admin'
        ):
            return Response({'error': 'Only an administrator can create customer accounts'}, status=403)
        serializer = CustomerSerializer(data=request.data)
        if serializer.is_valid():
            customer = serializer.save()
            get_or_create_card(customer)
            return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomerDetailView(APIView):
    def get(self, request, phone):
        customer = get_object_or_404(Customer, phone=phone)
        get_or_create_card(customer)
        return Response(CustomerSerializer(customer).data)


# ── Card ──────────────────────────────────────────────────────────────────────

class CardDetailView(APIView):
    """Get full card details for the logged-in customer."""
    def get(self, request, phone):
        customer = get_object_or_404(Customer, phone=phone)
        card = get_or_create_card(customer)
        return Response(CardDetailsFullSerializer(card).data)


class FreezeCardView(APIView):
    def post(self, request):
        serializer = FreezeCardSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        customer = get_object_or_404(Customer, phone=serializer.validated_data['customer_phone'])
        card = get_or_create_card(customer)
        action = serializer.validated_data['action']
        card.status = 'frozen' if action == 'freeze' else 'active'
        card.save()
        return Response({'status': card.status, 'message': f'Card {action}d successfully.'})


# ── Payment Sessions ──────────────────────────────────────────────────────────

class CreateSessionView(APIView):
    def post(self, request):
        serializer = CreateSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        merchant = get_object_or_404(Merchant, pk=serializer.validated_data['merchant_id'])
        try:
            destination, destination_account = resolve_merchant_destination(
                merchant,
                serializer.validated_data.get('settlement_destination'),
                serializer.validated_data.get('settlement_account_id'),
            )
        except RoutingError as error:
            return Response({'error': str(error)}, status=400)

        # Cancel existing pending sessions for this merchant
        PaymentSession.objects.filter(
            merchant=merchant, status='waiting'
        ).update(status='expired')

        session = PaymentSession.objects.create(
            merchant=merchant,
            amount=serializer.validated_data['amount'],
            status='waiting',
            settlement_destination=destination,
            settlement_account=destination_account,
        )
        return Response(session_response(session, merchant), status=status.HTTP_201_CREATED)


class SessionDetailView(APIView):
    def get(self, request, session_id):
        session = get_object_or_404(PaymentSession, pk=session_id)
        return Response(session_response(session))


class MerchantLatestSessionView(APIView):
    def get(self, request, merchant_id):
        merchant = get_object_or_404(Merchant, pk=merchant_id)
        cutoff = timezone.now() - timezone.timedelta(minutes=10)
        session = PaymentSession.objects.filter(
            merchant=merchant,
            status='waiting',
            created_at__gte=cutoff,
        ).order_by('-created_at').first()

        if session:
            return Response({
                'has_pending': True,
                'session': session_response(session, merchant),
                'merchant': MerchantSerializer(merchant).data,
            })
        return Response({
            'has_pending': False,
            'session': None,
            'merchant': MerchantSerializer(merchant).data,
        })


class ConfirmPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = ConfirmPaymentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        if request.user.role != 'customer' or request.user.phone != data['customer_phone']:
            return Response({'error': 'Payments can only be authorised by the signed-in customer.'}, status=403)
        session = get_object_or_404(PaymentSession, pk=data['session_id'])

        if session.status == 'confirmed':
            return Response({'error': 'This payment has already been confirmed.'}, status=400)
        if session.status == 'expired':
            return Response({'error': 'This session has expired. Please scan again.'}, status=400)

        customer, _ = Customer.objects.get_or_create(
            phone=data['customer_phone'],
            defaults={'default_funding_mode': data['funding_mode']}
        )
        get_or_create_card(customer)

        funding_mode = data['funding_mode']
        amount = session.amount

        try:
            if funding_mode in ('wallet', 'credit', 'linked'):
                payment_source, payment_source_account = resolve_customer_source(
                    customer,
                    funding_mode,
                    data.get('payment_source_account_id'),
                    amount=amount,
                )
            else:
                payment_source, payment_source_account = funding_mode, None
            destination, destination_account = resolve_merchant_destination(
                session.merchant,
                session.settlement_destination or None,
                session.settlement_account_id,
            )
        except RoutingError as error:
            return Response({'error': str(error)}, status=400)

        # ── Compliance checks (AML, limits, velocity, fraud) ──────────────
        compliance = run_payment_compliance(customer, amount, funding_mode, session)
        if not compliance.allowed:
            return Response({'error': compliance.reason}, status=400)

        # ── Credit card payment ───────────────────────────────────────────
        if funding_mode == 'credit':
            card = get_or_create_card(customer)
            if card.status == 'frozen':
                return Response({'error': 'Your Kona card is frozen.'}, status=400)
            if customer.available_credit < amount:
                return Response({
                    'error': f'Insufficient credit. Available: E{customer.available_credit}'
                }, status=400)

            # Debit credit balance
            customer.current_balance += amount
            customer.save()

            # Record on statement
            stmt = get_open_statement(customer)
            stmt.total_purchases += amount
            stmt.closing_balance = customer.current_balance
            stmt.save()

            # Create credit transaction
            CreditTransaction.objects.create(
                customer=customer,
                transaction_type='purchase',
                funding_mode='credit',
                amount=amount,
                session=session,
                merchant=session.merchant,
                statement=stmt,
                description=f'Purchase at {session.merchant.name}',
            )

            session.funding_mode = 'credit'

        elif funding_mode == 'wallet':
            CreditTransaction.objects.create(
                customer=customer,
                transaction_type='purchase',
                funding_mode='bank',
                amount=amount,
                session=session,
                merchant=session.merchant,
                description=f'Qinance Wallet purchase at {session.merchant.name}',
            )
            session.funding_mode = 'wallet'

        elif funding_mode == 'linked':
            CreditTransaction.objects.create(
                customer=customer,
                transaction_type='purchase',
                funding_mode='bank',
                amount=amount,
                session=session,
                merchant=session.merchant,
                description=f'Linked account payment at {session.merchant.name} via {payment_source_account.masked_label}',
            )
            session.bank_used = payment_source_account.provider
            session.funding_mode = 'linked'

        # ── Bank transfer (EPS) ───────────────────────────────────────────
        elif funding_mode == 'bank':
            bank = data.get('bank', '')
            if not bank:
                return Response({'error': 'Bank is required for bank transfer.'}, status=400)
            session.bank_used = bank
            session.funding_mode = 'bank'

            # Create credit transaction record (no balance change — direct transfer)
            CreditTransaction.objects.create(
                customer=customer,
                transaction_type='purchase',
                funding_mode='bank',
                amount=amount,
                session=session,
                merchant=session.merchant,
                description=f'EPS transfer at {session.merchant.name} via {bank.upper()}',
            )

        # ── JIT — bank funds card in real time ───────────────────────────
        elif funding_mode == 'jit':
            jit_bank = data.get('jit_bank', '')
            if not jit_bank:
                return Response({'error': 'JIT bank is required.'}, status=400)

            session.jit_funded = True
            session.jit_bank = jit_bank
            session.funding_mode = 'jit'

            CreditTransaction.objects.create(
                customer=customer,
                transaction_type='purchase',
                funding_mode='jit',
                amount=amount,
                session=session,
                merchant=session.merchant,
                description=f'JIT via {jit_bank.upper()} → Kona card at {session.merchant.name}',
            )

        # ── MTN MoMo ─────────────────────────────────────────────────────
        elif funding_mode == 'momo':
            momo_number = data.get('momo_number', '')
            if not momo_number:
                try:
                    momo_number = customer.momo_account.msisdn
                except LinkedMoMoAccount.DoesNotExist:
                    return Response({'error': 'No MoMo number provided or linked.'}, status=400)

            try:
                ref_id = momo_client.request_to_pay(
                    amount=float(amount),
                    msisdn=momo_number,
                    external_id=str(session.id),
                    payer_message=f'Payment at {session.merchant.name} — E{amount}',
                )
            except momo_client.MoMoError as e:
                return Response({'error': str(e)}, status=400)

            MoMoTransaction.objects.create(
                customer=customer,
                reference_id=ref_id,
                txn_type='collection',
                amount=amount,
                msisdn=momo_number,
                related_session=session,
            )

            # Save customer link on session but do not confirm yet — MoMo is async
            session.customer = customer
            session.save()

            return Response({
                'status':             'momo_pending',
                'momo_reference_id':  ref_id,
                'message':            'Approve the payment on your MTN MoMo — a prompt has been sent to your phone.',
                'session_id':         str(session.id),
                'amount':             str(amount),
                'funding_mode':       'momo',
            })

        # ── Confirm session ───────────────────────────────────────────────
        session.customer = customer
        session.payment_source = payment_source
        session.payment_source_account = payment_source_account
        session.settlement_destination = destination
        session.settlement_account = destination_account
        session.status = 'confirmed'
        session.confirmed_at = timezone.now()
        session.save()

        try:
            apply_payment_routes(
                customer, session.merchant, amount, payment_source, destination,
                str(session.id), destination_account,
            )
        except RoutingError as error:
            transaction.set_rollback(True)
            return Response({'error': str(error)}, status=400)

        # ── Persist compliance flags and regulatory reports ──────────────
        save_compliance_flags(customer, compliance)
        if compliance.requires_ctr or compliance.requires_str:
            create_regulatory_report(
                customer, amount, session,
                'ctr' if compliance.requires_ctr else 'str',
                compliance.flags,
            )

        # Push notification to customer (background-safe)
        try:
            from users.models import User as AuthUser
            auth_user = AuthUser.objects.get(phone=data['customer_phone'])
            send_payment_push(auth_user, session.merchant.name, session.amount)
        except Exception:
            pass

        # Fire WebSocket to merchant screen
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'session_{session.id}',
            {
                'type': 'payment_confirmed',
                'session_id': str(session.id),
                'amount': str(session.amount),
                'funding_mode': funding_mode,
                'bank_used': data.get('bank', '') or data.get('jit_bank', ''),
                'customer_phone': data['customer_phone'],
            }
        )

        return Response({
            'status': 'confirmed',
            'session_id': str(session.id),
            'amount': str(session.amount),
            'funding_mode': funding_mode,
            'message': 'Payment confirmed successfully.',
        })


# ── Wallets, linked accounts, and routing preferences ────────────────────────

def _routing_owner(user):
    if user.role == 'customer':
        return Customer.objects.filter(phone=user.phone).first()
    if user.role == 'merchant':
        return Merchant.objects.filter(phone=user.phone).first()
    return None


class RoutingProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner = _routing_owner(request.user)
        if not owner:
            return Response({'error': 'No customer or merchant payment profile found.'}, status=404)
        return Response(routing_snapshot(owner))

    def patch(self, request):
        owner = _routing_owner(request.user)
        if not owner:
            return Response({'error': 'No customer or merchant payment profile found.'}, status=404)
        selection = request.data.get('default_type')
        account_id = request.data.get('default_account_id')
        try:
            if isinstance(owner, Customer):
                source, account = resolve_customer_source(owner, selection, account_id)
                owner.default_payment_source = source
                owner.default_payment_account = account
                owner.save(update_fields=['default_payment_source', 'default_payment_account'])
            else:
                destination, account = resolve_merchant_destination(owner, selection, account_id)
                owner.default_settlement_destination = destination
                owner.default_settlement_account = account
                owner.save(update_fields=['default_settlement_destination', 'default_settlement_account'])
        except RoutingError as error:
            return Response({'error': str(error)}, status=400)
        return Response(routing_snapshot(owner))


class LinkedAccountListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        owner = _routing_owner(request.user)
        if not owner:
            return Response({'error': 'No customer or merchant payment profile found.'}, status=404)
        account_type = request.data.get('account_type', 'bank')
        provider = str(request.data.get('provider', '')).strip().lower()
        display_name = str(request.data.get('display_name', '')).strip()
        account_last4 = ''.join(filter(str.isdigit, str(request.data.get('account_last4', ''))))[-4:]
        if account_type not in ('bank', 'momo') or not provider or not display_name or len(account_last4) != 4:
            return Response({'error': 'Account type, provider, display name, and the last four digits are required.'}, status=400)
        values = {
            'account_type': account_type,
            'provider': provider,
            'display_name': display_name,
            'account_last4': account_last4,
            'provider_reference': str(request.data.get('provider_reference', '')).strip(),
            'can_debit': isinstance(owner, Customer),
            'can_credit': True,
        }
        if isinstance(owner, Customer):
            values['customer'] = owner
        else:
            values['merchant'] = owner
        account = LinkedAccount.objects.create(**values)
        return Response(routing_snapshot(owner), status=201)


class LinkedAccountRemoveView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, account_id):
        owner = _routing_owner(request.user)
        if not owner:
            return Response({'error': 'No payment profile found.'}, status=404)
        account = owner.linked_accounts.filter(id=account_id, status='active').first()
        if not account:
            return Response({'error': 'Linked account not found.'}, status=404)
        account.status = 'removed'
        account.save(update_fields=['status'])
        if isinstance(owner, Customer) and owner.default_payment_account_id == account.id:
            owner.default_payment_source = 'wallet'
            owner.default_payment_account = None
            owner.save(update_fields=['default_payment_source', 'default_payment_account'])
        elif isinstance(owner, Merchant) and owner.default_settlement_account_id == account.id:
            owner.default_settlement_destination = 'wallet'
            owner.default_settlement_account = None
            owner.save(update_fields=['default_settlement_destination', 'default_settlement_account'])
        return Response(routing_snapshot(owner))


class WalletTopUpView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        owner = _routing_owner(request.user)
        if not isinstance(owner, Customer):
            return Response({'error': 'Only customer wallets can be topped up.'}, status=403)
        try:
            amount = Decimal(str(request.data.get('amount'))).quantize(Decimal('0.01'))
        except Exception:
            return Response({'error': 'Enter a valid top-up amount.'}, status=400)
        if amount <= 0:
            return Response({'error': 'Top-up amount must be greater than zero.'}, status=400)
        try:
            _, account = resolve_customer_source(owner, 'linked', request.data.get('linked_account_id'))
            entry = post_wallet_entry(
                get_wallet(owner), amount, 'topup', f'Top up from {account.masked_label}',
                f'topup:{request.user.id}:{request.data.get("idempotency_key") or timezone.now().timestamp()}',
                {'linked_account_id': str(account.id), 'simulated': True},
            )
        except RoutingError as error:
            return Response({'error': str(error)}, status=400)
        return Response({'entry_id': str(entry.id), **routing_snapshot(owner)})


class WalletEntryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner = _routing_owner(request.user)
        if not owner:
            return Response({'error': 'No payment profile found.'}, status=404)
        wallet = get_wallet(owner)
        return Response([{
            'id': str(entry.id),
            'entry_type': entry.entry_type,
            'amount': str(entry.amount),
            'balance_after': str(entry.balance_after),
            'reference': entry.reference,
            'created_at': entry.created_at,
        } for entry in wallet.entries.all()[:100]])


class MerchantLoanListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def _merchant(self, request):
        if request.user.role != 'merchant':
            return None
        return Merchant.objects.filter(phone=request.user.phone, is_active=True).first()

    def get(self, request):
        merchant = self._merchant(request)
        if not merchant:
            return Response({'error': 'Active merchant account required.'}, status=403)
        return Response(MerchantLoanSerializer(merchant.loans.all(), many=True).data)

    def post(self, request):
        merchant = self._merchant(request)
        if not merchant:
            return Response({'error': 'Active merchant account required.'}, status=403)
        if merchant.loans.filter(status__in=['pending', 'approved', 'active']).exists():
            return Response({'error': 'You already have an open merchant finance application.'}, status=400)
        serializer = MerchantLoanSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        amount = serializer.validated_data['requested_amount']
        term = serializer.validated_data.get('term_months', 6)
        if amount <= 0 or term not in (3, 6, 9, 12):
            return Response({'error': 'Enter a valid amount and a 3, 6, 9, or 12 month term.'}, status=400)
        loan = serializer.save(merchant=merchant)
        return Response(MerchantLoanSerializer(loan).data, status=201)


# ── Repayments ────────────────────────────────────────────────────────────────

class MakeRepaymentView(APIView):
    def post(self, request):
        serializer = RepaymentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        customer = get_object_or_404(Customer, phone=data['customer_phone'])

        if customer.statement_balance <= 0 and customer.current_balance <= 0:
            return Response({'error': 'No balance to repay.'}, status=400)

        amount = customer.statement_balance if data['pay_full'] else data['amount']
        amount = Decimal(str(amount))

        # MoMo repayment — initiate async collection before recording
        if data['bank'] == 'momo':
            momo_number = data.get('momo_number', '')
            if not momo_number:
                try:
                    momo_number = customer.momo_account.msisdn
                except LinkedMoMoAccount.DoesNotExist:
                    return Response({'error': 'No MoMo number provided or linked.'}, status=400)
            try:
                ref_id = momo_client.request_to_pay(
                    amount=float(amount),
                    msisdn=momo_number,
                    external_id=f'repay-{customer.phone}-{timezone.now().timestamp()}',
                    payer_message=f'Qinance repayment E{amount}',
                )
                MoMoTransaction.objects.create(
                    customer=customer, reference_id=ref_id,
                    txn_type='collection', amount=amount, msisdn=momo_number,
                )
            except momo_client.MoMoError as e:
                return Response({'error': str(e)}, status=400)

        # Reduce balances
        customer.current_balance = max(customer.current_balance - amount, Decimal('0'))
        customer.statement_balance = max(customer.statement_balance - amount, Decimal('0'))
        customer.save()

        # Update open statement
        stmt = get_open_statement(customer)
        stmt.total_payments += amount
        stmt.closing_balance = customer.current_balance
        if customer.statement_balance <= 0:
            stmt.status = 'paid_full'
        stmt.save()

        # Record transaction
        CreditTransaction.objects.create(
            customer=customer,
            transaction_type='repayment',
            funding_mode='bank',
            amount=amount,
            statement=stmt,
            description=f'Repayment via {data["bank"].upper()}',
        )

        return Response({
            'status': 'repayment_recorded',
            'amount_paid': str(amount),
            'remaining_balance': str(customer.current_balance),
            'statement_balance': str(customer.statement_balance),
        })


# ── Statements ────────────────────────────────────────────────────────────────

class StatementListView(APIView):
    def get(self, request, phone):
        customer = get_object_or_404(Customer, phone=phone)
        statements = customer.statements.all()
        return Response(CreditStatementSerializer(statements, many=True).data)


class CurrentStatementView(APIView):
    def get(self, request, phone):
        customer = get_object_or_404(Customer, phone=phone)
        stmt = get_open_statement(customer)
        transactions = CreditTransaction.objects.filter(statement=stmt)
        return Response({
            'statement': CreditStatementSerializer(stmt).data,
            'transactions': CreditTransactionSerializer(transactions, many=True).data,
        })


# ── Admin / Dashboard ─────────────────────────────────────────────────────────

class TransactionListView(APIView):
    def get(self, request):
        sessions = PaymentSession.objects.select_related('merchant', 'customer')
        return Response(PaymentSessionSerializer(sessions, many=True).data)


class CreditTransactionListView(APIView):
    def get(self, request):
        txns = CreditTransaction.objects.select_related('customer', 'merchant')
        return Response(CreditTransactionSerializer(txns, many=True).data)


class DashboardStatsView(APIView):
    def get(self, request):
        confirmed = PaymentSession.objects.filter(status='confirmed')
        total_volume = sum(s.amount for s in confirmed)
        customers = Customer.objects.filter(is_active=True)
        total_credit_book = sum(c.current_balance for c in customers)
        total_credit_issued = sum(c.credit_limit for c in customers)
        overdue = [c for c in customers if c.is_overdue]
        overdue_amount = sum(c.statement_balance for c in overdue)

        from .models import RegulatoryReport
        pending_ctrs = RegulatoryReport.objects.filter(report_type='ctr', status='pending_submission').count()
        pending_strs = RegulatoryReport.objects.filter(report_type='str', status='pending_submission').count()

        return Response({
            'total_merchants': Merchant.objects.filter(is_active=True).count(),
            'total_customers': customers.count(),
            'total_transactions': confirmed.count(),
            'total_volume': str(total_volume),
            'total_credit_book': str(total_credit_book),
            'total_credit_issued': str(total_credit_issued),
            'overdue_customers': len(overdue),
            'overdue_amount': str(overdue_amount),
            'pending_ctrs': pending_ctrs,
            'pending_strs': pending_strs,
            'credit_utilisation': str(
                round(float(total_credit_book) / float(total_credit_issued) * 100, 1)
                if total_credit_issued > 0 else 0
            ),
        })


# ── Regulatory Reports ────────────────────────────────────────────────────────

class RegulatoryReportListView(APIView):

    def get(self, request):
        from .models import RegulatoryReport
        rtype = request.query_params.get('type')
        qs = RegulatoryReport.objects.all()
        if rtype:
            qs = qs.filter(report_type=rtype)
        data = [
            {
                'id': str(r.id),
                'report_type': r.report_type,
                'customer_phone': r.customer_phone,
                'customer_name': r.customer_name,
                'amount': str(r.amount),
                'flag_details': r.flag_details,
                'status': r.status,
                'created_at': r.created_at.isoformat(),
            }
            for r in qs
        ]
        return Response(data)


class SubmitReportView(APIView):

    def post(self, request, report_id):
        from .models import RegulatoryReport
        try:
            report = RegulatoryReport.objects.get(id=report_id)
        except RegulatoryReport.DoesNotExist:
            return Response({'error': 'Report not found'}, status=404)
        report.status = 'submitted'
        report.save()
        return Response({'message': 'Report marked as submitted to FIU'})


# ── Agent Banking ─────────────────────────────────────────────────────────────

CASHBACK_FEE_RATE       = Decimal('0.02')
BANK_DEPOSIT_FEE_RATE   = Decimal('0.01')
MERCHANT_INCENTIVE_RATE = Decimal('0.005')
MIN_FEE                 = Decimal('3.00')
MAX_CASHBACK_PER_TXN    = Decimal('1000.00')
MAX_DEPOSIT_PER_TXN     = Decimal('5000.00')


def calculate_agent_fees(amount, fee_rate):
    fee                = max(amount * fee_rate, MIN_FEE)
    merchant_incentive = amount * MERCHANT_INCENTIVE_RATE
    return fee.quantize(Decimal('0.01')), merchant_incentive.quantize(Decimal('0.01'))


class NearbyAgentMerchantsView(APIView):
    """Returns list of merchants with agent banking enabled."""

    def get(self, request):
        service = request.query_params.get('service')  # 'cashback' or 'bank_deposit'

        merchants = Merchant.objects.filter(
            is_active=True,
            agent_profile__isnull=False,
        ).select_related('agent_profile')

        data = []
        for m in merchants:
            profile = m.agent_profile
            profile.reset_daily_totals_if_needed()

            services = []
            if profile.is_cashback_enabled and profile.available_cash_float > 0:
                services.append('cashback')
            if profile.is_bank_deposit_enabled:
                services.append('bank_deposit')

            if not services:
                continue

            if service and service not in services:
                continue

            data.append({
                'merchant_id':     str(m.id),
                'name':            m.name,
                'location':        m.location,
                'phone':           m.phone,
                'services':        services,
                'available_float': str(profile.available_cash_float),
                'max_cashback':    str(min(
                    profile.available_cash_float,
                    profile.daily_cashback_limit - profile.total_cashback_today,
                    MAX_CASHBACK_PER_TXN
                )),
            })

        return Response(data)


class InitiateAgentTransactionView(APIView):
    """
    Customer initiates a cashback or bank deposit request.
    Returns transaction ID and fee breakdown for customer to review and confirm.
    """

    def post(self, request):
        transaction_type = request.data.get('transaction_type')
        merchant_id      = request.data.get('merchant_id')
        customer_phone   = request.data.get('customer_phone')
        amount           = request.data.get('amount')
        bank             = request.data.get('bank', '')

        if not all([transaction_type, merchant_id, customer_phone, amount]):
            return Response({
                'error': 'transaction_type, merchant_id, customer_phone and amount are required'
            }, status=400)

        if transaction_type not in ('cashback', 'bank_deposit'):
            return Response({'error': 'transaction_type must be cashback or bank_deposit'}, status=400)

        if transaction_type == 'bank_deposit' and not bank:
            return Response({'error': 'bank is required for bank deposit'}, status=400)

        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response({'error': 'Invalid amount'}, status=400)

        if amount <= 0:
            return Response({'error': 'Amount must be greater than zero'}, status=400)

        max_amounts = {
            'cashback':     MAX_CASHBACK_PER_TXN,
            'bank_deposit': MAX_DEPOSIT_PER_TXN,
        }
        if amount > max_amounts[transaction_type]:
            return Response({
                'error': f'Maximum amount is E{max_amounts[transaction_type]:.2f}'
            }, status=400)

        try:
            merchant = Merchant.objects.get(id=merchant_id, is_active=True)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

        try:
            customer = Customer.objects.get(phone=customer_phone)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)

        try:
            profile = merchant.agent_profile
            profile.reset_daily_totals_if_needed()
        except Exception:
            return Response({
                'error': 'This merchant does not offer agent banking services'
            }, status=400)

        if transaction_type == 'cashback':
            if not profile.is_cashback_enabled:
                return Response({'error': 'This merchant does not offer cash back'}, status=400)
            if profile.available_cash_float < amount:
                return Response({
                    'error': f'Merchant only has E{profile.available_cash_float:.2f} available'
                }, status=400)
            remaining_daily = profile.daily_cashback_limit - profile.total_cashback_today
            if amount > remaining_daily:
                return Response({
                    'error': f'Merchant daily cashback limit reached. Remaining: E{remaining_daily:.2f}'
                }, status=400)

        if transaction_type == 'bank_deposit':
            if not profile.is_bank_deposit_enabled:
                return Response({'error': 'This merchant does not accept cash deposits'}, status=400)

        fee_rates = {
            'cashback':     CASHBACK_FEE_RATE,
            'bank_deposit': BANK_DEPOSIT_FEE_RATE,
        }
        fee, merchant_incentive = calculate_agent_fees(amount, fee_rates[transaction_type])

        txn = AgentTransaction.objects.create(
            merchant=merchant,
            customer=customer,
            transaction_type=transaction_type,
            amount=amount,
            fee=fee,
            merchant_incentive=merchant_incentive,
            bank=bank,
            status='pending',
        )

        if transaction_type == 'cashback':
            summary = {
                'you_receive':     f'E{amount:.2f} cash',
                'fee':             f'E{fee:.2f}',
                'total_from_bank': f'E{amount + fee:.2f}',
            }
        else:
            summary = {
                'cash_to_merchant':  f'E{amount:.2f}',
                'fee':               f'E{fee:.2f}',
                'deposited_to_bank': f'E{amount - fee:.2f}',
            }

        return Response({
            'transaction_id':     str(txn.id),
            'transaction_type':   transaction_type,
            'amount':             str(amount),
            'fee':                str(fee),
            'merchant_incentive': str(merchant_incentive),
            'merchant_name':      merchant.name,
            'merchant_location':  merchant.location,
            'customer_name':      customer.full_name,
            'bank':               bank,
            'summary':            summary,
        })


class ConfirmAgentTransactionView(APIView):
    """
    Merchant confirms cash dispensed (cashback) or received (bank deposit).
    In production: triggers EPS pull (cashback) or EPS push (bank deposit).
    """

    def post(self, request):
        transaction_id = request.data.get('transaction_id')

        if not transaction_id:
            return Response({'error': 'transaction_id is required'}, status=400)

        try:
            txn = AgentTransaction.objects.select_related(
                'merchant', 'customer', 'merchant__agent_profile'
            ).get(id=transaction_id, status='pending')
        except AgentTransaction.DoesNotExist:
            return Response({
                'error': 'Transaction not found or already processed'
            }, status=404)

        profile = txn.merchant.agent_profile

        if txn.transaction_type == 'cashback':
            profile.available_cash_float -= txn.amount
            profile.total_cashback_today += txn.amount
            profile.save()
            message = (
                f'Cash back confirmed. '
                f'E{txn.amount:.2f} will be debited from your linked bank account.'
            )
        elif txn.transaction_type == 'bank_deposit':
            message = (
                f'Deposit confirmed. '
                f'E{txn.amount - txn.fee:.2f} will be credited to your '
                f'{txn.bank} account within minutes.'
            )

        txn.status       = 'confirmed'
        txn.confirmed_at = timezone.now()
        txn.save()

        CreditTransaction.objects.create(
            customer=txn.customer,
            merchant=txn.merchant,
            transaction_type='purchase' if txn.transaction_type == 'cashback' else 'repayment',
            funding_mode='bank',
            amount=txn.amount,
            description=f'{txn.get_transaction_type_display()} at {txn.merchant.name}',
            reference=str(txn.id),
        )

        return Response({
            'status':         'confirmed',
            'message':        message,
            'transaction_id': str(txn.id),
            'amount':         str(txn.amount),
            'fee':            str(txn.fee),
        })


class CancelAgentTransactionView(APIView):
    """Cancel a pending agent transaction before merchant confirms."""

    def post(self, request):
        transaction_id = request.data.get('transaction_id')

        if not transaction_id:
            return Response({'error': 'transaction_id is required'}, status=400)

        try:
            txn = AgentTransaction.objects.get(id=transaction_id, status='pending')
            txn.status = 'cancelled'
            txn.save()
            return Response({'message': 'Transaction cancelled'})
        except AgentTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found or already processed'}, status=404)


class MerchantAgentProfileView(APIView):
    """
    GET  — merchant views their agent profile and daily totals
    POST — merchant updates settings (enable/disable services, set float)
    """

    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
            profile, _ = MerchantAgentProfile.objects.get_or_create(merchant=merchant)
            profile.reset_daily_totals_if_needed()
            return Response({
                'is_cashback_enabled':     profile.is_cashback_enabled,
                'is_bank_deposit_enabled': profile.is_bank_deposit_enabled,
                'available_cash_float':    str(profile.available_cash_float),
                'daily_cashback_limit':    str(profile.daily_cashback_limit),
                'total_cashback_today':    str(profile.total_cashback_today),
            })
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

    def post(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
            profile, _ = MerchantAgentProfile.objects.get_or_create(merchant=merchant)

            for field in ['is_cashback_enabled', 'is_bank_deposit_enabled',
                          'available_cash_float', 'daily_cashback_limit']:
                if field in request.data:
                    setattr(profile, field, request.data[field])
            profile.save()

            return Response({'message': 'Agent profile updated'})
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)


class AgentTransactionHistoryView(APIView):
    """Get agent transaction history filtered by customer, merchant, or type."""

    def get(self, request):
        phone       = request.query_params.get('phone')
        merchant_id = request.query_params.get('merchant_id')
        txn_type    = request.query_params.get('type')

        qs = AgentTransaction.objects.filter(status='confirmed')

        if phone:
            qs = qs.filter(customer__phone=phone)
        if merchant_id:
            qs = qs.filter(merchant__id=merchant_id)
        if txn_type:
            qs = qs.filter(transaction_type=txn_type)

        data = [
            {
                'id':                str(t.id),
                'transaction_type':  t.transaction_type,
                'amount':            str(t.amount),
                'fee':               str(t.fee),
                'merchant_incentive': str(t.merchant_incentive),
                'bank':              t.bank,
                'merchant_name':     t.merchant.name,
                'merchant_location': t.merchant.location,
                'customer_phone':    t.customer.phone,
                'customer_name':     t.customer.full_name,
                'status':            t.status,
                'created_at':        t.created_at.isoformat(),
                'confirmed_at':      t.confirmed_at.isoformat() if t.confirmed_at else None,
            }
            for t in qs.order_by('-created_at')[:50]
        ]
        return Response(data)


# ── MTN MoMo ─────────────────────────────────────────────────────────────────

class RegisterMoMoView(APIView):
    """Link or update a customer's MTN MoMo number."""

    def post(self, request):
        phone       = request.data.get('customer_phone')
        momo_number = request.data.get('momo_number', '').strip()

        if not phone or not momo_number:
            return Response({'error': 'customer_phone and momo_number required'}, status=400)

        try:
            customer = Customer.objects.get(phone=phone)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)

        msisdn = momo_client.normalise_msisdn(momo_number)

        account, created = LinkedMoMoAccount.objects.update_or_create(
            customer=customer,
            defaults={'msisdn': msisdn, 'status': 'active'},
        )

        return Response({
            'message': 'MoMo number linked successfully.',
            'msisdn':  account.msisdn,
            'created': created,
        })


class MoMoStatusView(APIView):
    """
    Poll the status of an async MoMo transaction.
    Checks MTN API on each call and updates local record.
    When SUCCESSFUL, confirms the linked payment session and fires WebSocket.
    """

    def get(self, request, reference_id):
        try:
            txn = MoMoTransaction.objects.select_related(
                'customer', 'related_session', 'related_session__merchant'
            ).get(reference_id=reference_id)
        except MoMoTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=404)

        if txn.status == 'pending':
            try:
                result      = momo_client.get_payment_status(reference_id)
                momo_status = result.get('status', 'PENDING').upper()

                if momo_status == 'SUCCESSFUL':
                    txn.status = 'successful'
                    txn.save()

                    session = txn.related_session
                    if session and session.status != 'confirmed':
                        session.funding_mode = 'momo'
                        session.status       = 'confirmed'
                        session.confirmed_at = timezone.now()
                        session.save()

                        CreditTransaction.objects.create(
                            customer=txn.customer,
                            merchant=session.merchant,
                            transaction_type='purchase',
                            funding_mode='momo',
                            amount=txn.amount,
                            session=session,
                            description=f'MoMo payment at {session.merchant.name}',
                            reference=reference_id,
                        )

                        channel_layer = get_channel_layer()
                        async_to_sync(channel_layer.group_send)(
                            f'session_{session.id}',
                            {
                                'type':          'payment_confirmed',
                                'session_id':    str(session.id),
                                'amount':        str(session.amount),
                                'funding_mode':  'momo',
                                'customer_phone': txn.customer.phone,
                            }
                        )

                elif momo_status == 'FAILED':
                    txn.status = 'failed'
                    txn.save()

            except momo_client.MoMoError:
                pass  # credentials not set in dev — return current status

        return Response({
            'reference_id': reference_id,
            'status':       txn.status,
            'amount':       str(txn.amount),
        })


# ── Agent Session (QR / NFC channels) ────────────────────────────────────────

class CreateAgentSessionView(APIView):
    """Merchant creates an agent banking session for QR or NFC channel."""

    def post(self, request):
        merchant_id      = request.data.get('merchant_id')
        transaction_type = request.data.get('transaction_type')
        amount           = request.data.get('amount')
        channel          = request.data.get('channel', 'qr')

        if not all([merchant_id, transaction_type, amount]):
            return Response({'error': 'merchant_id, transaction_type, amount required'}, status=400)
        if transaction_type not in ('cashback', 'bank_deposit'):
            return Response({'error': 'Invalid transaction_type'}, status=400)
        if channel not in ('qr', 'nfc'):
            return Response({'error': 'channel must be qr or nfc'}, status=400)
        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response({'error': 'Invalid amount'}, status=400)

        merchant = get_object_or_404(Merchant, pk=merchant_id, is_active=True)

        AgentSession.objects.filter(
            merchant=merchant,
            transaction_type=transaction_type,
            status='pending',
        ).update(status='expired')

        session = AgentSession.objects.create(
            merchant=merchant,
            transaction_type=transaction_type,
            amount=amount,
            channel=channel,
        )

        return Response({
            'session_id':       str(session.id),
            'merchant_id':      str(merchant.id),
            'merchant_name':    merchant.name,
            'transaction_type': transaction_type,
            'amount':           str(amount),
            'channel':          channel,
            'url':              f'/a/{merchant.id}/{session.id}',
            'status':           'pending',
        }, status=status.HTTP_201_CREATED)


class GetAgentSessionView(APIView):
    """Customer fetches agent session details after scanning QR or tapping NFC."""

    def get(self, request, session_id):
        session = get_object_or_404(AgentSession, pk=session_id)
        if session.status == 'expired':
            return Response({'error': 'This session has expired.'}, status=400)
        fee_rate = CASHBACK_FEE_RATE if session.transaction_type == 'cashback' else BANK_DEPOSIT_FEE_RATE
        fee, _ = calculate_agent_fees(session.amount, fee_rate)
        return Response({
            'session_id':        str(session.id),
            'merchant_id':       str(session.merchant.id),
            'merchant_name':     session.merchant.name,
            'merchant_location': session.merchant.location,
            'transaction_type':  session.transaction_type,
            'amount':            str(session.amount),
            'fee':               str(fee),
            'status':            session.status,
            'channel':           session.channel,
        })


class ConfirmAgentSessionView(APIView):
    """Customer confirms a QR/NFC agent banking session."""

    def post(self, request, session_id):
        customer_phone = request.data.get('customer_phone')
        bank           = request.data.get('bank', '')

        if not customer_phone:
            return Response({'error': 'customer_phone is required'}, status=400)

        session = get_object_or_404(AgentSession, pk=session_id)

        if session.status == 'confirmed':
            return Response({'error': 'Session already confirmed'}, status=400)
        if session.status in ('cancelled', 'expired'):
            return Response({'error': 'Session is no longer valid'}, status=400)

        try:
            customer = Customer.objects.get(phone=customer_phone)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)

        merchant = session.merchant
        amount   = session.amount

        bank = bank or customer.bank
        if not bank:
            try:
                bank = customer.momo_account.msisdn and 'momo'
            except Exception:
                pass
        if session.transaction_type == 'bank_deposit' and not bank:
            return Response({'error': 'bank is required for bank deposit'}, status=400)

        try:
            profile = merchant.agent_profile
            profile.reset_daily_totals_if_needed()
        except Exception:
            return Response({'error': 'This merchant does not offer agent banking'}, status=400)

        if session.transaction_type == 'cashback':
            if not profile.is_cashback_enabled:
                return Response({'error': 'Merchant does not offer cash back'}, status=400)
            if profile.available_cash_float < amount:
                return Response({'error': f'Merchant only has E{profile.available_cash_float:.2f} available'}, status=400)
            remaining_daily = profile.daily_cashback_limit - profile.total_cashback_today
            if amount > remaining_daily:
                return Response({'error': f'Daily limit reached. Remaining: E{remaining_daily:.2f}'}, status=400)
            fee, merchant_incentive = calculate_agent_fees(amount, CASHBACK_FEE_RATE)
            profile.available_cash_float -= amount
            profile.total_cashback_today += amount
            profile.save()
            message = f'E{amount:.2f} cash back confirmed. E{amount + fee:.2f} will be debited from your bank.'
        else:
            if not profile.is_bank_deposit_enabled:
                return Response({'error': 'Merchant does not accept deposits'}, status=400)
            fee, merchant_incentive = calculate_agent_fees(amount, BANK_DEPOSIT_FEE_RATE)
            message = f'E{amount - fee:.2f} will be credited to your {bank} account.'

        funding_mode = 'momo' if bank == 'momo' else 'bank'

        txn = AgentTransaction.objects.create(
            merchant=merchant,
            customer=customer,
            transaction_type=session.transaction_type,
            amount=amount,
            fee=fee,
            merchant_incentive=merchant_incentive,
            bank=bank,
            status='confirmed',
            confirmed_at=timezone.now(),
        )

        session.customer     = customer
        session.status       = 'confirmed'
        session.confirmed_at = timezone.now()
        session.save()

        CreditTransaction.objects.create(
            customer=customer,
            merchant=merchant,
            transaction_type='purchase' if session.transaction_type == 'cashback' else 'repayment',
            funding_mode=funding_mode,
            amount=amount,
            description=f'{txn.get_transaction_type_display()} at {merchant.name}',
            reference=str(txn.id),
        )

        # MoMo — initiate async transfer/collection
        if bank == 'momo':
            try:
                momo_number = customer.momo_account.msisdn
                if session.transaction_type == 'bank_deposit':
                    net_amount = float(amount - fee)
                    ref_id = momo_client.transfer(
                        amount=net_amount, msisdn=momo_number,
                        external_id=str(txn.id),
                        payee_note=f'Deposit at {merchant.name}',
                    )
                    momo_txn_type = 'disbursement'
                else:
                    ref_id = momo_client.request_to_pay(
                        amount=float(amount + fee), msisdn=momo_number,
                        external_id=str(txn.id),
                        payer_message=f'Cash back at {merchant.name}',
                    )
                    momo_txn_type = 'collection'
                MoMoTransaction.objects.create(
                    customer=customer, reference_id=ref_id, txn_type=momo_txn_type,
                    amount=amount, msisdn=momo_number, related_agent_txn=txn,
                )
                message += ' MoMo transaction initiated — approve on your phone.'
            except (momo_client.MoMoError, Exception):
                pass  # non-fatal if MoMo not configured

        return Response({
            'status':           'confirmed',
            'transaction_id':   str(txn.id),
            'amount':           str(amount),
            'fee':              str(fee),
            'message':          message,
            'transaction_type': session.transaction_type,
            'merchant_name':    merchant.name,
        })


class SoundAgentInitiateView(APIView):
    """Merchant submits customer sound token to process agent banking immediately (offline-capable)."""

    def post(self, request):
        token            = request.data.get('token')
        amount           = request.data.get('amount')
        merchant_id      = request.data.get('merchant_id')
        transaction_type = request.data.get('transaction_type')

        if not all([token, amount, merchant_id, transaction_type]):
            return Response({'error': 'token, amount, merchant_id, transaction_type required'}, status=400)
        if transaction_type not in ('cashback', 'bank_deposit'):
            return Response({'error': 'Invalid transaction_type'}, status=400)

        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response({'error': 'Invalid amount'}, status=400)

        try:
            customer_sound_id = int(token.split(':')[0])
        except Exception:
            return Response({'error': 'Invalid token format'}, status=400)

        try:
            merchant = Merchant.objects.get(id=merchant_id, is_active=True)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

        try:
            customer = Customer.objects.get(sound_id=customer_sound_id)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)

        try:
            device_secret = CustomerDeviceSecret.objects.get(customer=customer)
        except CustomerDeviceSecret.DoesNotExist:
            return Response({'error': 'Customer device not registered'}, status=400)

        if not _verify_sound_token(token, device_secret.secret, customer.sound_id):
            return Response({'error': 'Invalid token'}, status=400)
        if not _verify_token_timestamp(token):
            return Response({'error': 'Token expired — ask customer to regenerate'}, status=400)

        try:
            profile = merchant.agent_profile
            profile.reset_daily_totals_if_needed()
        except Exception:
            return Response({'error': 'Merchant does not offer agent banking'}, status=400)

        if transaction_type == 'cashback':
            if not profile.is_cashback_enabled:
                return Response({'error': 'Merchant does not offer cash back'}, status=400)
            if profile.available_cash_float < amount:
                return Response({'error': f'Insufficient float. Available: E{profile.available_cash_float:.2f}'}, status=400)
            remaining_daily = profile.daily_cashback_limit - profile.total_cashback_today
            if amount > remaining_daily:
                return Response({'error': f'Daily limit reached. Remaining: E{remaining_daily:.2f}'}, status=400)
            fee, merchant_incentive = calculate_agent_fees(amount, CASHBACK_FEE_RATE)
            profile.available_cash_float -= amount
            profile.total_cashback_today += amount
            profile.save()
            message = f'Cash back E{amount:.2f} confirmed for {customer.full_name or customer.phone}.'
        else:
            if not profile.is_bank_deposit_enabled:
                return Response({'error': 'Merchant does not accept deposits'}, status=400)
            fee, merchant_incentive = calculate_agent_fees(amount, BANK_DEPOSIT_FEE_RATE)
            message = f'Deposit E{amount:.2f} confirmed for {customer.full_name or customer.phone}.'

        txn = AgentTransaction.objects.create(
            merchant=merchant,
            customer=customer,
            transaction_type=transaction_type,
            amount=amount,
            fee=fee,
            merchant_incentive=merchant_incentive,
            status='confirmed',
            confirmed_at=timezone.now(),
        )

        CreditTransaction.objects.create(
            customer=customer,
            merchant=merchant,
            transaction_type='purchase' if transaction_type == 'cashback' else 'repayment',
            funding_mode='bank',
            amount=amount,
            description=f'{txn.get_transaction_type_display()} at {merchant.name} (Contactless)',
            reference=str(txn.id),
        )

        return Response({
            'status':           'confirmed',
            'transaction_id':   str(txn.id),
            'customer_name':    customer.full_name or customer.phone,
            'amount':           str(amount),
            'fee':              str(fee),
            'message':          message,
            'transaction_type': transaction_type,
        })


# ── Sound / Contactless Payment ───────────────────────────────────────────────

import hmac as _hmac
import hashlib
import pyotp
from decimal import Decimal as _Decimal

SETTLEMENT_DELAY_SECONDS = 30


def _calculate_trust_score(merchant):
    score = 0
    if merchant.transaction_count >= 200:  score += 30
    elif merchant.transaction_count >= 50: score += 20
    elif merchant.transaction_count >= 10: score += 10
    if merchant.dispute_count == 0:        score += 20
    if merchant.kyc_approved:              score += 20
    age_days = (timezone.now().date() - merchant.created_at.date()).days
    if age_days >= 180:   score += 15
    elif age_days >= 30:  score += 5
    if merchant.trust_level == 'anchor':   score = 100
    return min(score, 100)


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _verify_sound_token(token, secret, customer_sound_id):
    """Verify legacy cid:ts:hmac8 or routed cid:ts:source:hmac8 tokens."""
    try:
        parts = token.split(':')
        if len(parts) not in (3, 4):
            return False
        token_cid, token_hmac = parts[0], parts[-1]
        if int(token_cid) != int(customer_sound_id):
            return False
        message = ':'.join(parts[:-1])
        expected = _hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:8]
        return _hmac.compare_digest(token_hmac, expected)
    except Exception:
        return False


def _verify_token_timestamp(token):
    try:
        parts = token.split(':')
        if len(parts) not in (3, 4):
            return False
        timestamp_window = int(parts[1])
        current_window   = int(timezone.now().timestamp() / 30)
        return abs(current_window - timestamp_window) <= 1
    except Exception:
        return False


def _sound_source(token):
    parts = token.split(':')
    if len(parts) == 3:
        return None, None
    code = parts[2]
    if code == 'w':
        return 'wallet', None
    if code == 'c':
        return 'credit', None
    if code.startswith('l-') and len(code) == 10:
        return 'linked', code[2:]
    raise RoutingError('Invalid payment source in contactless token.')


def _schedule_settlement(settlement_id):
    import threading
    t = threading.Timer(SETTLEMENT_DELAY_SECONDS, _execute_settlement, args=[settlement_id])
    t.daemon = True
    t.start()


def _execute_settlement(settlement_id):
    try:
        settlement = PendingSettlement.objects.get(id=settlement_id, status='pending')
    except PendingSettlement.DoesNotExist:
        return
    try:
        compliance = run_payment_compliance(settlement.customer, settlement.amount, 'bank', None)
        if not compliance.allowed:
            settlement.status       = 'reversed'
            settlement.conflict_note = f'Compliance block: {compliance.reason}'
            settlement.save()
            return
        # Simulate EPS payment (wire real EPS API here when available)
        settlement.status    = 'settled'
        settlement.settled_at = timezone.now()
        settlement.save()
        settlement.merchant.transaction_count += 1
        settlement.merchant.trust_score = _calculate_trust_score(settlement.merchant)
        settlement.merchant.save()
        save_compliance_flags(settlement.customer, compliance)
        if compliance.requires_ctr or compliance.requires_str:
            create_regulatory_report(
                settlement.customer, settlement.amount, settlement,
                'ctr' if compliance.requires_ctr else 'str', compliance.flags,
            )
    except Exception as e:
        settlement.status       = 'reversed'
        settlement.conflict_note = f'Settlement error: {str(e)}'
        settlement.save()


class SyncDeviceSecretView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        phone = request.data.get('phone')
        if phone != request.user.phone:
            return Response({'error': 'Device credentials can only be synced for the signed-in customer'}, status=403)
        try:
            customer = Customer.objects.get(phone=phone)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)
        if customer.sound_id is None:
            with transaction.atomic():
                max_id = Customer.objects.select_for_update().aggregate(m=Max('sound_id'))['m'] or 0
                customer.sound_id = max_id + 1
                customer.save(update_fields=['sound_id'])
        device_secret, _ = CustomerDeviceSecret.objects.get_or_create(
            customer=customer,
            defaults={'secret': pyotp.random_base32()},
        )
        return Response({
            'secret':            device_secret.secret,
            'customer_id':       str(customer.id),
            'customer_sound_id': customer.sound_id,
            'synced_at':         timezone.now().isoformat(),
        })


class ProcessSoundPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token       = request.data.get('token')
        amount      = request.data.get('amount')
        merchant_id = request.data.get('merchant_id')
        if not all([token, amount, merchant_id]):
            return Response({'error': 'token, amount, merchant_id required'}, status=400)
        try:
            amount = _Decimal(str(amount))
        except Exception:
            return Response({'error': 'Invalid amount'}, status=400)
        # Extract customer sound_id from token (format: cid:ts:hmac8)
        try:
            customer_sound_id = int(token.split(':')[0])
        except Exception:
            return Response({'error': 'Invalid token format'}, status=400)
        try:
            merchant = Merchant.objects.get(id=merchant_id, is_active=True)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)
        if request.user.role != 'merchant' or request.user.phone != merchant.phone:
            return Response({'error': 'This terminal is not authorised for that merchant.'}, status=403)
        if merchant.sound_id is None:
            with transaction.atomic():
                max_id = Merchant.objects.select_for_update().aggregate(m=Max('sound_id'))['m'] or 0
                merchant.sound_id = max_id + 1
                merchant.save(update_fields=['sound_id'])
        try:
            customer = Customer.objects.get(sound_id=customer_sound_id)
        except Customer.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=404)
        try:
            device_secret = CustomerDeviceSecret.objects.get(customer=customer)
        except CustomerDeviceSecret.DoesNotExist:
            return Response({'error': 'Customer device not registered'}, status=400)
        if not _verify_sound_token(token, device_secret.secret, customer.sound_id):
            return Response({'error': 'Invalid token signature'}, status=400)
        if not _verify_token_timestamp(token):
            return Response({'error': 'Token expired — ask customer to regenerate'}, status=400)
        try:
            token_source, routing_key = _sound_source(token)
            payment_source, payment_source_account = resolve_customer_source(
                customer, token_source, routing_key=routing_key, amount=amount,
            )
            destination, destination_account = resolve_merchant_destination(
                merchant,
                request.data.get('settlement_destination'),
                request.data.get('settlement_account_id'),
            )
        except RoutingError as error:
            return Response({'error': str(error)}, status=400)
        compliance = run_payment_compliance(customer, amount, payment_source, None)
        if not compliance.allowed:
            return Response({'error': compliance.reason}, status=400)
        token_hash = _hash_token(token)
        existing = PendingSettlement.objects.filter(token_hash=token_hash).first()
        if existing:
            return _handle_conflict(existing, merchant, customer, amount, token_hash)
        trust_score = _calculate_trust_score(merchant)
        merchant.trust_score = trust_score
        merchant.save()
        settlement = PendingSettlement.objects.create(
            token_hash   = token_hash,
            customer     = customer,
            merchant     = merchant,
            amount       = amount,
            trust_score  = trust_score,
            settle_after = timezone.now() + timedelta(seconds=SETTLEMENT_DELAY_SECONDS),
            payment_source=payment_source,
            payment_source_account=payment_source_account,
            settlement_destination=destination,
            settlement_account=destination_account,
        )
        try:
            with transaction.atomic():
                statement = None
                if payment_source == 'credit':
                    locked_customer = Customer.objects.select_for_update().get(pk=customer.pk)
                    if not credit_is_available(locked_customer, amount):
                        raise RoutingError('Qinance Credit is not available for this payment.')
                    locked_customer.current_balance += amount
                    locked_customer.save(update_fields=['current_balance'])
                    statement = get_open_statement(locked_customer)
                    statement.total_purchases += amount
                    statement.closing_balance = locked_customer.current_balance
                    statement.save()
                apply_payment_routes(
                    customer, merchant, amount, payment_source, destination,
                    str(settlement.id), destination_account,
                )
                CreditTransaction.objects.create(
                    customer=customer,
                    merchant=merchant,
                    transaction_type='purchase',
                    funding_mode='credit' if payment_source == 'credit' else 'bank',
                    amount=amount,
                    statement=statement,
                    description=f'Contactless payment at {merchant.name}',
                    reference=str(settlement.id),
                )
        except RoutingError as error:
            settlement.status = 'reversed'
            settlement.conflict_note = str(error)
            settlement.save(update_fields=['status', 'conflict_note'])
            return Response({'error': str(error)}, status=400)
        _schedule_settlement(settlement.id)
        return Response({
            'status':        'confirmed',
            'message':       'Payment confirmed. Funds settle in 30 seconds.',
            'settlement_id': str(settlement.id),
            'amount':        str(amount),
            'merchant':      merchant.name,
            'settles_at':    settlement.settle_after.isoformat(),
        })


def _handle_conflict(existing, new_merchant, customer, amount, token_hash):
    # A sound token represents one payment attempt. Replays must never move
    # money twice or be reassigned to a different merchant based on trust.
    if existing.merchant_id == new_merchant.id and existing.amount == amount:
        return Response({
            'status': 'confirmed',
            'message': 'Payment was already confirmed.',
            'settlement_id': str(existing.id),
            'amount': str(existing.amount),
        })
    return Response({'status': 'rejected', 'message': 'This contactless token has already been used.'}, status=400)


class SoundPaymentStatusView(APIView):
    def get(self, request, settlement_id):
        try:
            s = PendingSettlement.objects.get(id=settlement_id)
            return Response({
                'settlement_id': str(s.id),
                'status':        s.status,
                'amount':        str(s.amount),
                'merchant':      s.merchant.name,
                'settled_at':    s.settled_at.isoformat() if s.settled_at else None,
                'conflict_note': s.conflict_note,
            })
        except PendingSettlement.DoesNotExist:
            return Response({'error': 'Settlement not found'}, status=404)


class SoundSettlementListView(APIView):
    def get(self, request):
        settlements = PendingSettlement.objects.select_related('customer', 'merchant').all()[:100]
        return Response([{
            'id':            str(s.id),
            'merchant':      s.merchant.name,
            'customer':      s.customer.full_name or s.customer.phone,
            'amount':        str(s.amount),
            'trust_score':   s.trust_score,
            'status':        s.status,
            'received_at':   s.received_at.isoformat(),
            'settled_at':    s.settled_at.isoformat() if s.settled_at else None,
            'conflict_note': s.conflict_note,
        } for s in settlements])


class MerchantTrustView(APIView):
    def get(self, request, merchant_id):
        try:
            m = Merchant.objects.get(id=merchant_id)
            return Response({'trust_level': m.trust_level, 'trust_score': m.trust_score,
                             'transaction_count': m.transaction_count, 'dispute_count': m.dispute_count})
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)

    def post(self, request, merchant_id):
        try:
            m = Merchant.objects.get(id=merchant_id)
            if 'trust_level' in request.data:
                m.trust_level = request.data['trust_level']
                m.trust_score = _calculate_trust_score(m)
                m.save()
            return Response({'message': 'Trust level updated', 'trust_score': m.trust_score})
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=404)
