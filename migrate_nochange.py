from db import connect, init_db, stats

init_db()
with connect() as conn:
    cur = conn.execute(
        "UPDATE records SET status='nochange', error='auto unchanged' "
        "WHERE changed=0 AND status='done'"
    )
    conn.commit()
    print("migrated", cur.rowcount)
print(stats())
