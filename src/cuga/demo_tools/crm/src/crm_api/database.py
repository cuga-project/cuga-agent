from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os


# Get CRM DB path from DYNACONF environment variable with default
def get_crm_db_path():
    """Get CRM database path from DYNACONF_CRM_DB_PATH or use default."""
    env_path = os.getenv("DYNACONF_CRM_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)

    # Default: ${workdir}/crm_tmp/crm_db_default
    workdir = os.getcwd()
    default_path = os.path.join(workdir, "crm_tmp", "crm_db_default")
    return os.path.abspath(default_path)


# Use SQLite for simplicity, but can be easily changed to PostgreSQL
CRM_DB_PATH = get_crm_db_path()
DATABASE_URL = f"sqlite:///{CRM_DB_PATH}"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from crm_api.models import Base

    # Reset database file on each startup
    db_path = CRM_DB_PATH
    # Ensure parent directory exists
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    Base.metadata.create_all(bind=engine)

    # Check if we need to seed data
    from crm_api.crud import account_crud

    db = SessionLocal()
    try:
        if account_crud.count(db) == 0:
            from crm_api.seed_data import seed_database

            seed_database(db)
    finally:
        db.close()
