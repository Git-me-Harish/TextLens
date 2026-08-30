from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    # Defense in depth alongside the actual root causes fixed in
    # worker/tasks.py (ingest's countdown-scheduling loop mismatch, etc.):
    # a pooled asyncpg connection can still go stale from a DB restart,
    # network blip, or idle-timeout disconnect. pre_ping runs a cheap
    # "SELECT 1" before handing out a pooled connection and transparently
    # discards+reconnects if it fails, instead of handing back a broken
    # connection that then raises deep inside a query.
    pool_pre_ping=True,
    # Recycle connections before typical cloud-Postgres / pgbouncer idle
    # disconnect windows (usually 5-10 min) — belt-and-suspenders with
    # pre_ping, cheap since it only reconnects idle connections.
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
