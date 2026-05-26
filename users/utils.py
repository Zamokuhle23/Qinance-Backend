from .models import (
    TrustedDevice,
    AuditLog,
)


def register_device(request, user):

    fingerprint = TrustedDevice.generate_fingerprint(
        request
    )

    device, created = TrustedDevice.objects.get_or_create(
        user=user,
        fingerprint=fingerprint,
        defaults={
            'device_name': request.META.get(
                'HTTP_USER_AGENT',
                'Unknown Device'
            )[:255],
            'ip_address': request.META.get(
                'REMOTE_ADDR',
                '0.0.0.0'
            ),
            'user_agent': request.META.get(
                'HTTP_USER_AGENT',
                ''
            ),
        }
    )

    return device


def create_audit_log(
    user,
    action,
    request,
    metadata=None
):

    AuditLog.log(
        user=user,
        action=action,
        request=request,
        metadata=metadata,
    )