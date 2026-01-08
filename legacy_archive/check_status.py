import sqlite3

conn = sqlite3.connect("production.db")
c = conn.cursor()
c.execute("SELECT file_stem, stage, status FROM jobs WHERE file_stem LIKE '%DOAN%'")
rows = c.fetchall()
for row in rows:
    print(row)
conn.close()
