import sqlite3


class MyRargbDB:
  
  
  def __init__(self):
    self.conn = sqlite3.connect("myrargb.db", check_same_thread=False)
    self.cur = self.conn.cursor()
    self.cur.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        size TEXT,
        title TEXT,        
        url TEXT,
        type TEXT    
    )
    """)
    #TYPE: 00: MOVIES, 01: TV SHOWS, etc.
    self.conn.commit()


  def save_items(self, items):

    for item in items:
        self.cur.execute("""
        INSERT INTO movies (filename, size, url, type) VALUES (?, ?, ?, ?)
        """, (item["filename"], item["size"], item["url"], item["type"]))

    self.conn.commit()


  def get_items(self, type='movies', is_training=True, sql='', limit=1000, order_by='id DESC'):
    exe_sql = ' select * from movies where 1=1 '
    if type=='movies':
      exe_sql += " and type = '00' "
    if sql:
      exe_sql += ' ' + sql + ' '
    exe_sql += f' ORDER BY {order_by} '
    exe_sql += f' LIMIT {limit} '
    self.cur.execute(exe_sql)
    rows = self.cur.fetchall()

    items = []
    for row in rows:
        items.append({
            'id': row[0],
            "filename": row[1],
            "size": row[2],
            'title': row[3],
            "url": row[4],
            "type": row[5],
            "score": row[6],
            "genre": row[7],
            "poster": row[8],
            "marked": row[9]
        })

    return items
    

  def update_item(self, item):
    # id must exist
    if "id" not in item:
        raise ValueError("item must contain 'id'")

    allowed_fields = ["title", "score", "poster", "marked", "genre", "filename", "size", "url", "type"]
    fields = []
    values = []

    for key in allowed_fields:
        if key in item:
            fields.append(f"{key} = ?")
            values.append(item[key])

    # Nothing to update
    if not fields:
        return False

    # Add id at the end for WHERE id = ?
    values.append(item["id"])

    sql = f"UPDATE movies SET {', '.join(fields)} WHERE id = ?"
    self.cur.execute(sql, values)
    self.conn.commit()
    return True


  def batch_replace(self):
    print('# Excuting batch replacement...')
    items = self.get_items()
    
    for item in items:
      if '.' in item['title'] or '_' in item['title']:
        print(f"# Found it, updating ID {item['id']}: {item['title']}")
        new_title = item['title'].replace('.', ' ').replace('_', ' ')
        self.cur.execute("UPDATE movies SET title = ? WHERE id = ?", (new_title, item['id']))
    self.conn.commit()
     
      
  def __del__(self):
    self.conn.close()
    
db = MyRargbDB()    
    
if __name__ == "__main__":    
    pass    