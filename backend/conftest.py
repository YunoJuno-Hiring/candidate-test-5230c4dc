import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Company, CompanyAdmin, Freelancer
from contracts.models import Contract, TimesheetEntry


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def northstar():
    return Company.objects.create(name='NorthStar Consulting', billing_email='billing@northstar.test')


@pytest.fixture
def meridian():
    return Company.objects.create(name='Meridian Digital', billing_email='accounts@meridian.test')


@pytest.fixture
def admin_user(northstar):
    user = User.objects.create_user(
        username='admin@test.test', email='admin@test.test', password='testpass'
    )
    CompanyAdmin.objects.create(user=user, company=northstar)
    return user


@pytest.fixture
def admin_client(api_client, admin_user):
    token, _ = Token.objects.get_or_create(user=admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return api_client


@pytest.fixture
def freelancer_user():
    user = User.objects.create_user(
        username='freelancer@test.test', email='freelancer@test.test', password='testpass'
    )
    Freelancer.objects.create(user=user, name='Test Freelancer')
    return user


@pytest.fixture
def freelancer_client(api_client, freelancer_user):
    token, _ = Token.objects.get_or_create(user=freelancer_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return api_client


@pytest.fixture
def active_contract(northstar, freelancer_user):
    return Contract.objects.create(
        company=northstar,
        freelancer=freelancer_user.freelancer,
        daily_rate=Decimal('600.00'),
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        status='active',
    )


@pytest.fixture
def other_contract(meridian, freelancer_user):
    """A contract at a different company — the admin fixture's company should NOT see this."""
    return Contract.objects.create(
        company=meridian,
        freelancer=freelancer_user.freelancer,
        daily_rate=Decimal('700.00'),
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        status='active',
    )


@pytest.fixture
def submitted_entry(active_contract):
    return TimesheetEntry.objects.create(
        contract=active_contract,
        date=datetime.date(2026, 4, 7),
        hours=Decimal('8.0'),
        status='submitted',
    )


# --- Billing fixtures (Task B) ---
# Additive only: the fixtures above are depended on by the contracts and accounts
# suites, so nothing here modifies them.

@pytest.fixture
def approved_entries(active_contract):
    """Three approved entries in May 2026 at 600/day -> 8h, 7.5h, 8h.

    Subtotal: 4800.00 + 4500.00 + 4800.00 = 14100.00
    """
    return [
        TimesheetEntry.objects.create(
            contract=active_contract, date=datetime.date(2026, 5, d),
            hours=Decimal(h), status='approved',
        )
        for d, h in [(4, '8.0'), (5, '7.5'), (6, '8.0')]
    ]


@pytest.fixture
def second_freelancer():
    """A second freelancer on the same company, for the Company x Freelancer grain test."""
    user = User.objects.create_user(
        username='second@test.test', email='second@test.test', password='testpass'
    )
    return Freelancer.objects.create(user=user, name='Second Freelancer')


@pytest.fixture
def second_contract(northstar, second_freelancer):
    return Contract.objects.create(
        company=northstar,
        freelancer=second_freelancer,
        daily_rate=Decimal('500.00'),
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        status='active',
    )


@pytest.fixture
def partial_month_contract(northstar, freelancer_user):
    """Contract running 12 Feb - 18 Mar 2026 (the partial-month case from the brief)."""
    return Contract.objects.create(
        company=northstar,
        freelancer=freelancer_user.freelancer,
        daily_rate=Decimal('600.00'),
        start_date=datetime.date(2026, 2, 12),
        end_date=datetime.date(2026, 3, 18),
        status='closed',
    )


@pytest.fixture
def partial_month_entries(partial_month_contract):
    """Approved entries straddling the Feb/Mar boundary, plus one after the contract ends.

    March must bill only 5 Mar and 20 Mar; February must bill only 10 Feb and 20 Feb.
    """
    return [
        TimesheetEntry.objects.create(
            contract=partial_month_contract, date=datetime.date(2026, 2, 10),
            hours=Decimal('8.0'), status='approved',
        ),
        TimesheetEntry.objects.create(
            contract=partial_month_contract, date=datetime.date(2026, 2, 20),
            hours=Decimal('8.0'), status='approved',
        ),
        TimesheetEntry.objects.create(
            contract=partial_month_contract, date=datetime.date(2026, 3, 5),
            hours=Decimal('8.0'), status='approved',
        ),
        TimesheetEntry.objects.create(
            contract=partial_month_contract, date=datetime.date(2026, 3, 20),
            hours=Decimal('8.0'), status='approved',
        ),
    ]
