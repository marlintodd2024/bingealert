"""
BingeAlert - config.py DB_HOST Default Fix
==========================================
In app/config.py, find this line:

    db_host: str = "postgres"

Replace with:

    db_host: str = "bingealert-db"

This ensures the default matches the docker-compose service name.
The compose files also set DB_HOST=bingealert-db explicitly in the
environment block, so this is a belt-and-suspenders fix.
"""
