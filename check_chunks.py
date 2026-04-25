import psycopg2

conn = psycopg2.connect(host='localhost', dbname='quizsense', user='postgres', password='postgres')
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
print("Tables:", [r[0] for r in cur.fetchall()])
cur.execute("SELECT COUNT(*) FROM quiz_uploadedchunk")
print(f"UploadedChunk: {cur.fetchone()[0]}")
conn.close()
