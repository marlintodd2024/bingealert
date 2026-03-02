"""
BingeAlert - Database Startup Retry Patch
==========================================
Replace the database initialization section in the lifespan() function in main.py.

Find this block:
    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")

Replace with the block below.
"""

# ============================================================
# REPLACEMENT CODE - paste into lifespan() in main.py
# ============================================================

    # Wait for database to be ready (retry with backoff)
    max_retries = 30
    retry_delay = 2  # seconds
    db_ready = False
    
    for attempt in range(1, max_retries + 1):
        try:
            # Test the connection
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            # Connection works - create tables
            Base.metadata.create_all(bind=engine)
            logger.info(f"Database connected and tables created/verified (attempt {attempt})")
            db_ready = True
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"Database not ready (attempt {attempt}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                # Exponential backoff, cap at 10s
                retry_delay = min(retry_delay * 1.5, 10)
            else:
                logger.error(
                    f"Failed to connect to database after {max_retries} attempts. "
                    f"Check DB_HOST, DB_PORT, DB_USER, DB_PASSWORD in your .env file. "
                    f"Last error: {e}"
                )
    
    if not db_ready:
        logger.error(
            "⚠️  BingeAlert starting without database connection. "
            "Most features will not work until the database is available."
        )

# ============================================================
# Also add this import at the top of main.py if not present:
# ============================================================

from sqlalchemy import text
