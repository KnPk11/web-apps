import sqlite3
import json
import subprocess
from review_worker import moderate_story, get_db

def run_debug():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, content FROM stories WHERE moderated = 0')
    pending = cursor.fetchall()
    print(f"Pending count: {len(pending)}")
    for row in pending:
        status = moderate_story(row['content'])
        print(f"Processing ID {row['id']}, Status: {status}")
        if status != 0:
            cursor.execute('UPDATE stories SET moderated = ? WHERE id = ?', (status, row['id']))
            conn.commit()
            print(f"Successfully updated ID {row['id']} in DB")
        else:
            print(f"ID {row['id']} returned status 0 (no change)")
    conn.close()

if __name__ == '__main__':
    run_debug()
