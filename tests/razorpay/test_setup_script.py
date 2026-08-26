import pytest

from scripts.setup_test_subscription import SetupResult, create_test_subscription


class FakePlanResource:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create(self, data: dict) -> dict:
        self.created.append(data)
        return {"id": "plan_TEST0001"}


class FakeSubscriptionResource:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create(self, data: dict) -> dict:
        self.created.append(data)
        return {"id": "sub_TEST0001", "short_url": "https://rzp.io/i/TESTLINK"}


class FakeClient:
    def __init__(self) -> None:
        self.plan = FakePlanResource()
        self.subscription = FakeSubscriptionResource()


def test_the_setup_creates_a_plan_and_a_subscription():
    result = create_test_subscription(FakeClient(), plan_amount_paise=99900)
    assert isinstance(result, SetupResult)
    assert result.plan_id == "plan_TEST0001"
    assert result.subscription_id == "sub_TEST0001"
    assert result.auth_url == "https://rzp.io/i/TESTLINK"


def test_the_plan_is_created_in_paise_and_inr():
    client = FakeClient()
    create_test_subscription(client, plan_amount_paise=99900)
    item = client.plan.created[0]["item"]
    assert item["amount"] == 99900
    assert item["currency"] == "INR"


def test_the_plan_bills_monthly_so_a_retry_cycle_is_observable():
    client = FakeClient()
    create_test_subscription(client, plan_amount_paise=99900)
    assert client.plan.created[0]["period"] == "monthly"
    assert client.plan.created[0]["interval"] == 1


def test_the_subscription_references_the_new_plan():
    client = FakeClient()
    create_test_subscription(client, plan_amount_paise=99900)
    assert client.subscription.created[0]["plan_id"] == "plan_TEST0001"


def test_the_subscription_total_count_is_configurable():
    client = FakeClient()
    create_test_subscription(client, plan_amount_paise=99900, total_count=3)
    assert client.subscription.created[0]["total_count"] == 3


def test_a_zero_or_negative_amount_is_refused():
    with pytest.raises(ValueError):
        create_test_subscription(FakeClient(), plan_amount_paise=0)


def test_a_subscription_response_with_no_short_url_yields_an_empty_auth_url():
    client = FakeClient()
    client.subscription.create = lambda data: {"id": "sub_X"}
    assert create_test_subscription(client, plan_amount_paise=99900).auth_url == ""
