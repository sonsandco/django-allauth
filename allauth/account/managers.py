from datetime import timedelta

from django.conf import settings
from django.contrib.sites.managers import CurrentSiteManager
from django.db import models
from django.db.models import Q
from django.utils import timezone

from . import app_settings


class EmailAddressManagerMixin:
    def can_add_email(self, user):
        ret = True
        if app_settings.MAX_EMAIL_ADDRESSES:
            count = self.filter(user=user).count()
            ret = count < app_settings.MAX_EMAIL_ADDRESSES
        return ret

    def add_email(self, request, user, email, confirm=False, signup=False):
        email_address, created = self.get_or_create(
            user=user, email__iexact=email, defaults={"email": email}
        )

        if created and confirm:
            email_address.send_confirmation(request, signup=signup)

        return email_address

    def get_primary(self, user):
        try:
            return self.get(user=user, primary=True)
        except self.model.DoesNotExist:
            return None

    def get_users_for(self, email):
        # this is a list rather than a generator because we probably want to
        # do a len() on it right away
        return [
            address.user for address in self.filter(verified=True, email__iexact=email)
        ]

    def fill_cache_for_user(self, user, addresses):
        """
        In a multi-db setup, inserting records and re-reading them later
        on may result in not being able to find newly inserted
        records. Therefore, we maintain a cache for the user so that
        we can avoid database access when we need to re-read..
        """
        user._emailaddress_cache = addresses

    def get_for_user(self, user, email):
        cache_key = "_emailaddress_cache"
        addresses = getattr(user, cache_key, None)
        if addresses is None:
            ret = self.get(user=user, email__iexact=email)
            # To avoid additional lookups when e.g.
            # EmailAddress.set_as_primary() starts touching self.user
            ret.user = user
            return ret
        else:
            for address in addresses:
                if address.email.lower() == email.lower():
                    return address
            raise self.model.DoesNotExist()


if getattr(settings, 'VARY_TOP_LEVEL_MODEL_BY_SITE', False):
    class EmailAddressManager(EmailAddressManagerMixin, CurrentSiteManager):
        use_in_migrations = True

        def get_queryset(self):
            if not (hasattr(self, '_get_field_name') and getattr(
                    settings, 'STAFF_ACCOUNT_MULTI_SITE', False)):
                return super().get_queryset()

            # The first filter is copied from CurrentSiteManager. To that, we
            # add an exception for staff members, who have one account shared
            # between all sites.
            return super(CurrentSiteManager, self).get_queryset().filter(
                models.Q(**{
                    self._get_field_name() + '__id': settings.SITE_ID
                }) | models.Q(user__is_staff=True))
else:
    class EmailAddressManager(EmailAddressManagerMixin, models.Manager):
        pass


class EmailConfirmationManager(models.Manager):
    def all_expired(self):
        return self.filter(self.expired_q())

    def all_valid(self):
        return self.exclude(self.expired_q())

    def expired_q(self):
        sent_threshold = timezone.now() - timedelta(
            days=app_settings.EMAIL_CONFIRMATION_EXPIRE_DAYS
        )
        return Q(sent__lt=sent_threshold)

    def delete_expired_confirmations(self):
        self.all_expired().delete()
