import sqlite3


class MyRargbDB:
  
  
  def __init__(self):
    self.conn = sqlite3.connect("myrargb.db")
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


  def get_items(self, type='movies', is_training=True, limit=1000):
    if is_training:
      self.cur.execute("SELECT id, filename, size, title, url FROM movies WHERE type = ? LIMIT ?", ('00' if type == 'movies' else '01', limit))
    else:
      self.cur.execute("SELECT id, filename, size, title, url FROM movies WHERE (title IS NULL OR title = '') AND type = ? LIMIT ?", ('00' if type == 'movies' else '01', limit))
    rows = self.cur.fetchall()

    items = []
    for row in rows:
        items.append({
            'id': row[0],
            "filename": row[1],
            "size": row[2],
            'title': row[3],
            "url": row[4]
        })

    return items
    

  def update_item(self, item):
    self.cur.execute("""
    UPDATE movies SET title = ? WHERE id = ?
    """, (item['title'], item['id']))
    self.conn.commit()


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