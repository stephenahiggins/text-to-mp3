import pytest


@pytest.fixture(autouse=True)
def isolated_discovery(monkeypatch):
    # Never advertise test databases or compete with the user's running inbox.
    monkeypatch.setenv('ARTICLE_DISCOVERY', '0')
    monkeypatch.setenv('ARTICLE_SHARE_PORT', '0')
