import os, asyncio, asyncpg
async def main():
    dsn = f"postgres://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    print("DSN:", dsn)
    try:
        conn = await asyncpg.connect(dsn)
        row = await conn.fetchrow("SELECT current_database() db, inet_server_addr() addr, inet_server_port() port")
        print("connected:", dict(row) if row else row)
    except Exception as e:
        print("ERROR:", e)
    finally:
        if 'conn' in locals():
            await conn.close()
asyncio.run(main())
