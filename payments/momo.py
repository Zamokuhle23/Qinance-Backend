"""
MTN MoMo API client — Collections (debit) and Disbursements (credit).

Credentials are loaded from Django settings / environment variables:
  MOMO_BASE_URL                      (default: MTN sandbox)
  MOMO_ENVIRONMENT                   (sandbox | mtnswazi for Eswatini production)
  MOMO_CURRENCY                      (default: SZL — Swazi Lilangeni)
  MOMO_COLLECTIONS_SUBSCRIPTION_KEY
  MOMO_COLLECTIONS_USER_ID
  MOMO_COLLECTIONS_API_KEY
  MOMO_DISBURSEMENTS_SUBSCRIPTION_KEY
  MOMO_DISBURSEMENTS_USER_ID
  MOMO_DISBURSEMENTS_API_KEY

All functions raise MoMoError on failure.
"""

import uuid
import base64
import requests
from django.conf import settings


MOMO_BASE_URL    = getattr(settings, 'MOMO_BASE_URL',    'https://sandbox.momodeveloper.mtn.com')
MOMO_ENVIRONMENT = getattr(settings, 'MOMO_ENVIRONMENT', 'sandbox')
MOMO_CURRENCY    = getattr(settings, 'MOMO_CURRENCY',    'SZL')

COLLECTIONS_SUBSCRIPTION_KEY    = getattr(settings, 'MOMO_COLLECTIONS_SUBSCRIPTION_KEY', '')
COLLECTIONS_USER_ID             = getattr(settings, 'MOMO_COLLECTIONS_USER_ID', '')
COLLECTIONS_API_KEY             = getattr(settings, 'MOMO_COLLECTIONS_API_KEY', '')

DISBURSEMENTS_SUBSCRIPTION_KEY  = getattr(settings, 'MOMO_DISBURSEMENTS_SUBSCRIPTION_KEY', '')
DISBURSEMENTS_USER_ID           = getattr(settings, 'MOMO_DISBURSEMENTS_USER_ID', '')
DISBURSEMENTS_API_KEY           = getattr(settings, 'MOMO_DISBURSEMENTS_API_KEY', '')


class MoMoError(Exception):
    pass


def credentials_configured() -> bool:
    return bool(COLLECTIONS_USER_ID and COLLECTIONS_API_KEY and COLLECTIONS_SUBSCRIPTION_KEY)


def disbursements_configured() -> bool:
    return bool(DISBURSEMENTS_USER_ID and DISBURSEMENTS_API_KEY and DISBURSEMENTS_SUBSCRIPTION_KEY)


def normalise_msisdn(msisdn: str) -> str:
    """Strip leading + and ensure Eswatini country code 268 is present."""
    n = msisdn.lstrip('+').replace(' ', '').replace('-', '')
    if not n.startswith('268'):
        n = '268' + n
    return n


def _get_access_token(user_id: str, api_key: str, subscription_key: str, product: str) -> str:
    credentials = base64.b64encode(f'{user_id}:{api_key}'.encode()).decode()
    try:
        r = requests.post(
            f'{MOMO_BASE_URL}/{product}/token/',
            headers={
                'Authorization':             f'Basic {credentials}',
                'Ocp-Apim-Subscription-Key': subscription_key,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()['access_token']
    except Exception as e:
        raise MoMoError(f'Token fetch failed: {e}')


def request_to_pay(amount: float, msisdn: str, external_id: str, payer_message: str = '') -> str:
    """
    Collections API — send a USSD debit prompt to the customer's MoMo phone.
    Customer approves on their handset within ~90 seconds.
    Returns reference_id (UUID) — poll get_payment_status() for result.
    """
    if not credentials_configured():
        raise MoMoError('MoMo Collections credentials not configured. Set MOMO_COLLECTIONS_* in settings.')

    reference_id = str(uuid.uuid4())
    token = _get_access_token(COLLECTIONS_USER_ID, COLLECTIONS_API_KEY, COLLECTIONS_SUBSCRIPTION_KEY, 'collection')

    try:
        r = requests.post(
            f'{MOMO_BASE_URL}/collection/v1_0/requesttopay',
            json={
                'amount':     str(int(amount)),
                'currency':   MOMO_CURRENCY,
                'externalId': external_id,
                'payer': {
                    'partyIdType': 'MSISDN',
                    'partyId':     normalise_msisdn(msisdn),
                },
                'payerMessage': payer_message or 'Qinance Payment',
                'payeeNote':    'Qinance',
            },
            headers={
                'Authorization':             f'Bearer {token}',
                'X-Reference-Id':            reference_id,
                'X-Target-Environment':      MOMO_ENVIRONMENT,
                'Ocp-Apim-Subscription-Key': COLLECTIONS_SUBSCRIPTION_KEY,
                'Content-Type':              'application/json',
            },
            timeout=15,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise MoMoError(f'requesttopay failed ({e.response.status_code}): {e.response.text}')
    except Exception as e:
        raise MoMoError(f'requesttopay error: {e}')

    return reference_id


def get_payment_status(reference_id: str) -> dict:
    """
    Poll Collections payment status.
    Returns dict: {'status': 'PENDING'|'SUCCESSFUL'|'FAILED', 'reason': {...}}
    """
    if not credentials_configured():
        raise MoMoError('MoMo credentials not configured.')

    token = _get_access_token(COLLECTIONS_USER_ID, COLLECTIONS_API_KEY, COLLECTIONS_SUBSCRIPTION_KEY, 'collection')
    try:
        r = requests.get(
            f'{MOMO_BASE_URL}/collection/v1_0/requesttopay/{reference_id}',
            headers={
                'Authorization':             f'Bearer {token}',
                'X-Target-Environment':      MOMO_ENVIRONMENT,
                'Ocp-Apim-Subscription-Key': COLLECTIONS_SUBSCRIPTION_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise MoMoError(f'Status check failed: {e}')


def transfer(amount: float, msisdn: str, external_id: str, payee_note: str = '') -> str:
    """
    Disbursements API — push money to a customer's MoMo wallet.
    Used for agent banking deposits and cashback settlements.
    Returns reference_id — poll get_transfer_status() for result.
    """
    if not disbursements_configured():
        raise MoMoError('MoMo Disbursements credentials not configured. Set MOMO_DISBURSEMENTS_* in settings.')

    reference_id = str(uuid.uuid4())
    token = _get_access_token(DISBURSEMENTS_USER_ID, DISBURSEMENTS_API_KEY, DISBURSEMENTS_SUBSCRIPTION_KEY, 'disbursement')

    try:
        r = requests.post(
            f'{MOMO_BASE_URL}/disbursement/v1_0/transfer',
            json={
                'amount':     str(int(amount)),
                'currency':   MOMO_CURRENCY,
                'externalId': external_id,
                'payee': {
                    'partyIdType': 'MSISDN',
                    'partyId':     normalise_msisdn(msisdn),
                },
                'payerMessage': payee_note or 'Qinance Transfer',
                'payeeNote':    payee_note or 'Qinance Transfer',
            },
            headers={
                'Authorization':             f'Bearer {token}',
                'X-Reference-Id':            reference_id,
                'X-Target-Environment':      MOMO_ENVIRONMENT,
                'Ocp-Apim-Subscription-Key': DISBURSEMENTS_SUBSCRIPTION_KEY,
                'Content-Type':              'application/json',
            },
            timeout=15,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise MoMoError(f'transfer failed ({e.response.status_code}): {e.response.text}')
    except Exception as e:
        raise MoMoError(f'transfer error: {e}')

    return reference_id


def get_transfer_status(reference_id: str) -> dict:
    """Poll Disbursements transfer status."""
    if not disbursements_configured():
        raise MoMoError('MoMo Disbursements credentials not configured.')

    token = _get_access_token(DISBURSEMENTS_USER_ID, DISBURSEMENTS_API_KEY, DISBURSEMENTS_SUBSCRIPTION_KEY, 'disbursement')
    try:
        r = requests.get(
            f'{MOMO_BASE_URL}/disbursement/v1_0/transfer/{reference_id}',
            headers={
                'Authorization':             f'Bearer {token}',
                'X-Target-Environment':      MOMO_ENVIRONMENT,
                'Ocp-Apim-Subscription-Key': DISBURSEMENTS_SUBSCRIPTION_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise MoMoError(f'Transfer status check failed: {e}')
