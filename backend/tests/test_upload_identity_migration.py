from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_identity_review_migration_preserves_existing_records(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    path = tmp_path / 'migration.db'
    config.set_main_option('sqlalchemy.url', 'sqlite:///' + path.as_posix())
    command.upgrade(config, '20261125_0008')
    engine = create_engine('sqlite:///' + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO research_jobs (id, topic, status) VALUES ('saved', 'Existing job', 'PENDING')"))
    command.upgrade(config, '20261125_0009')
    assert 'upload_identity_reviews' in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT topic FROM research_jobs WHERE id='saved'")) == 'Existing job'
    command.downgrade(config, '20261125_0008')
    assert 'upload_identity_reviews' not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT topic FROM research_jobs WHERE id='saved'")) == 'Existing job'
    engine.dispose()
