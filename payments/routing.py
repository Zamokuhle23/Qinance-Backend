from decimal import Decimal

from django.db import transaction
from django.urls import re_path
from django.utils import timezone

from .models import (
    CardDetails,
    Customer,
    ExternalSettlement,
    LinkedAccount,
    Merchant,
    Wallet,
    WalletEntry,
)
from . import consumers


websocket_urlpatterns = [
    re_path(
        r'ws/session/(?P<session_id>[0-9a-f-]+)/$',
        consumers.PaymentSessionConsumer.as_asgi(),
    ),
]


class RoutingError(ValueError):
    pass


def get_wallet(owner):
    if isinstance(owner, Customer):
        wallet, _ = Wallet.objects.get_or_create(customer=owner)
    elif isinstance(owner, Merchant):
        wallet, _ = Wallet.objects.get_or_create(merchant=owner)
    else:
        raise RoutingError('Unsupported wallet owner.')
    return wallet


def post_wallet_entry(wallet, amount, entry_type, reference, idempotency_key, metadata=None):
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    with transaction.atomic():
        existing = WalletEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
        locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
        next_balance = locked.balance + amount
        if next_balance < 0:
            raise RoutingError(f'Insufficient wallet balance. Available: E{locked.balance:.2f}')
        locked.balance = next_balance
        locked.save(update_fields=['balance', 'updated_at'])
        return WalletEntry.objects.create(
            wallet=locked,
            entry_type=entry_type,
            amount=amount,
            balance_after=next_balance,
            reference=reference,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )


def credit_is_available(customer, amount=None):
    from users.models import User

    user = User.objects.filter(phone=customer.phone).first()
    card = CardDetails.objects.filter(customer=customer).first()
    eligible = bool(
        user
        and user.credit_status == 'approved'
        and customer.credit_limit > 0
        and card
        and card.status == 'active'
    )
    if amount is not None:
        eligible = eligible and customer.available_credit >= Decimal(str(amount))
    return eligible


def _active_account(owner, account_id=None, routing_key=None, capability='debit'):
    accounts = owner.linked_accounts.filter(status='active')
    if account_id:
        accounts = accounts.filter(id=account_id)
    elif routing_key:
        accounts = accounts.filter(routing_key=routing_key)
    else:
        return None
    account = accounts.first()
    if not account:
        raise RoutingError('The selected linked account is unavailable.')
    if capability == 'debit' and not account.can_debit:
        raise RoutingError('The selected account cannot fund payments.')
    if capability == 'credit' and not account.can_credit:
        raise RoutingError('The selected account cannot receive settlements.')
    return account


def resolve_customer_source(customer, source=None, account_id=None, routing_key=None, amount=None):
    source = source or customer.default_payment_source
    if source == 'wallet':
        wallet = get_wallet(customer)
        if amount is not None and wallet.balance < Decimal(str(amount)):
            raise RoutingError(f'Insufficient wallet balance. Available: E{wallet.balance:.2f}')
        return source, None
    if source == 'credit':
        if not credit_is_available(customer, amount):
            raise RoutingError('Qinance Credit is not available for this payment.')
        return source, None
    if source == 'linked':
        account = _active_account(
            customer,
            account_id=account_id or customer.default_payment_account_id,
            routing_key=routing_key,
            capability='debit',
        )
        if not account:
            raise RoutingError('Select a linked account for this payment.')
        return source, account
    raise RoutingError('Unsupported payment source.')


def resolve_merchant_destination(merchant, destination=None, account_id=None):
    destination = destination or merchant.default_settlement_destination
    if destination == 'wallet':
        return destination, None
    if destination == 'linked':
        account = _active_account(
            merchant,
            account_id=account_id or merchant.default_settlement_account_id,
            capability='credit',
        )
        if not account:
            raise RoutingError('Select a linked account to receive this payment.')
        return destination, account
    raise RoutingError('Unsupported settlement destination.')


@transaction.atomic
def apply_payment_routes(customer, merchant, amount, source, destination, reference, destination_account=None):
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    customer_wallet = get_wallet(customer)
    merchant_wallet = get_wallet(merchant)

    if source == 'wallet':
        post_wallet_entry(
            customer_wallet, -amount, 'payment', reference,
            f'payment:{reference}:customer-debit',
            {'merchant_id': str(merchant.id)},
        )

    post_wallet_entry(
        merchant_wallet, amount, 'receipt', reference,
        f'payment:{reference}:merchant-credit',
        {'customer_id': str(customer.id), 'source': source},
    )

    if destination == 'linked':
        if not destination_account:
            raise RoutingError('A settlement account is required.')
        post_wallet_entry(
            merchant_wallet, -amount, 'settlement', reference,
            f'payment:{reference}:merchant-settlement',
            {'linked_account_id': str(destination_account.id)},
        )
        ExternalSettlement.objects.get_or_create(
            reference=f'payment:{reference}',
            defaults={
                'merchant': merchant,
                'linked_account': destination_account,
                'amount': amount,
                'status': 'settled',
                'settled_at': timezone.now(),
            },
        )


def routing_snapshot(owner):
    wallet = get_wallet(owner)
    accounts = owner.linked_accounts.filter(status='active')
    result = {
        'wallet': {
            'id': str(wallet.id),
            'balance': str(wallet.balance),
            'currency': wallet.currency,
        },
        'linked_accounts': [{
            'id': str(account.id),
            'routing_key': account.routing_key,
            'account_type': account.account_type,
            'provider': account.provider,
            'display_name': account.display_name,
            'account_last4': account.account_last4,
            'label': account.masked_label,
            'can_debit': account.can_debit,
            'can_credit': account.can_credit,
        } for account in accounts],
    }
    if isinstance(owner, Customer):
        result.update({
            'role': 'customer',
            'default_type': owner.default_payment_source,
            'default_account_id': str(owner.default_payment_account_id) if owner.default_payment_account_id else None,
            'credit': {
                'eligible': credit_is_available(owner),
                'available': str(owner.available_credit),
            },
        })
    else:
        result.update({
            'role': 'merchant',
            'default_type': owner.default_settlement_destination,
            'default_account_id': str(owner.default_settlement_account_id) if owner.default_settlement_account_id else None,
        })
    return result
