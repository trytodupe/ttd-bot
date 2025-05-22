import sqlite3
import inspect


create_index_table = '''CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY, 
    group_id INTEGER NOT NULL
    );'''

create_group_table_template = '''CREATE TABLE IF NOT EXISTS group_{group_id} (
    id INTEGER PRIMARY KEY,
    date DATE NOT NULL
    );'''


class fstr:
    def __init__(self, payload):
        self.payload = payload
    def __str__(self):
        vars = inspect.currentframe().f_back.f_globals.copy()
        vars.update(inspect.currentframe().f_back.f_locals)
        return self.payload.format(**vars)

# --- DB WRITE ---
async def init_db(conn):
    cursor = conn.cursor()
    cursor.execute(create_index_table)
    conn.commit()

async def add_group(conn, group_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,))
    if cursor.fetchone() is None:
        cursor.execute(str(fstr(create_group_table_template)))
        cursor.execute(str(fstr("INSERT INTO groups (group_id) VALUES (?)")), (group_id,))
        conn.commit()
    # else:
        # print(f"group_id {group_id} already exists.")

async def add_date(conn, group_id, date):
    await add_group(conn, group_id)

    cursor = conn.cursor()
    cursor.execute(str(fstr("SELECT 1 FROM group_{group_id} WHERE date = ?")), (date,))
    if cursor.fetchone() is None:
        cursor.execute(str(fstr("INSERT INTO group_{group_id} (date) VALUES (?)")), (date,))
        
        columns = cursor.execute(str(fstr("PRAGMA table_info(group_{group_id})"))).fetchall()
        
        for column in columns:
            if column[1] != 'date':  # 排除 date 列
                cursor.execute(str(fstr(f"UPDATE group_{group_id} SET {column[1]} = 0 WHERE {column[1]} IS NULL AND date = ?")), (date,))
        
        # cursor.execute(str(fstr(create_group_table_template)))
        conn.commit()
    # else:
    #     print(f"date {date} in group {group_id} already exists.")

async def add_user(conn, group_id, user_id):
    await add_group(conn, group_id)

    cursor = conn.cursor()
    column_name = f"user_{user_id}"
    cursor.execute(str(fstr("PRAGMA table_info(group_{group_id})")))
    columns = [info[1] for info in cursor.fetchall()]
    if column_name not in columns:
        cursor.execute(str(fstr(f"ALTER TABLE group_{group_id} ADD COLUMN {column_name} INTEGER DEFAULT 0")))
        conn.commit()
    # else:
    #     print(f"Column {column_name} already exists in group_{group_id}.")

async def iterate_number(conn, group_id, date, user_id):
    await add_date(conn, group_id, date)
    await add_user(conn, group_id, user_id)

    cursor = conn.cursor()
    cursor.execute(str(fstr("UPDATE group_{group_id} SET user_{user_id} = user_{user_id} + 1 WHERE date = ?")), (date,))
    conn.commit()

# --- DB READ ---
async def table_exists(cursor, table_name):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None

async def print_all_data(conn):
    print('\n--- All data ---')
    try:
        cur = conn.cursor()
        cur.execute("SELECT group_id FROM groups")
        group_ids = cur.fetchall()
        
        for group_id in group_ids:
            group_id = group_id[0]
            print(f"Data for group_{group_id}:")
            cur.execute(str(fstr("SELECT * FROM group_{group_id}")))
            rows = cur.fetchall()
            # 打印表头
            column_names = [description[0] for description in cur.description]
            print(column_names)
            # 打印数据行
            if rows:
                for row in rows:
                    print(row)
            else:
                print("No data available.")
            print()

    except sqlite3.Error as e:
        print(f"Error: {e}")

async def get_data_by_date(conn, group_id, date):
    cursor = conn.cursor()

    if not await table_exists(cursor, f"group_{group_id}"):
        print(f"Table group_{group_id} does not exist.")
        return {}

    cursor.execute(str(fstr("SELECT * FROM group_{group_id} WHERE date = ?")), (date,))
    row = cursor.fetchone()
    column_names = [description[0] for description in cursor.description]
    if row and any("_" in column_name for column_name in column_names):
        column_names = [description[0] for description in cursor.description]
        data_dict = {int(column_name.split('_')[1]): row[i]
                     for i, column_name in enumerate(column_names) if "_" in column_name and row[i] > 0}
        return dict(sorted(data_dict.items(), key=lambda item: item[1], reverse=True))
    else:
        print(f"No data found for group {group_id} on date {date}.")
        return {}

async def get_all_data(conn, group_id):
    cursor = conn.cursor()

    if not await table_exists(cursor, f"group_{group_id}"):
        print(f"Table group_{group_id} does not exist.")
        return {}

    cursor.execute(str(fstr("SELECT * FROM group_{group_id}")))
    rows = cursor.fetchall()
    column_names = [description[0] for description in cursor.description]
    if rows and any("_" in column_name for column_name in column_names):
        data_dict = {}
        for row in rows:
            for i, column_name in enumerate(column_names):
                if "_" in column_name:
                    user_id = int(column_name.split('_')[1])
                    if user_id not in data_dict:
                        data_dict[user_id] = 0
                    data_dict[user_id] += row[i]
        return dict(sorted(data_dict.items(), key=lambda item: item[1], reverse=True))
    else:
        print(f"No data found for group {group_id}.")
        return {}
