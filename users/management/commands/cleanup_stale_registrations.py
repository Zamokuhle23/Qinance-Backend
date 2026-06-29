from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from users.models import User


class Command(BaseCommand):
    help = 'Delete expired, unfinished public registrations and their unlinked payment profiles.'

    def handle(self, *args, **options):
        from payments.models import Customer, Merchant

        expired = list(User.objects.filter(
            registration_expires_at__isnull=False,
            registration_expires_at__lte=timezone.now(),
            kyc_status='pending',
        ))
        with transaction.atomic():
            for user in expired:
                Merchant.objects.filter(phone=user.phone).delete()
                Customer.objects.filter(phone=user.phone).delete()
                user.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {len(expired)} stale registration(s).'))
