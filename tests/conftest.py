import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")
