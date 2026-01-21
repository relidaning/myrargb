import sqlite3
import logging as logger
from workflow import Workflow


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
        INSERT INTO movies (filename, size, url, type, genre) VALUES (?, ?, ?, ?, ?)
      """, (item["filename"], item["size"], item["url"], item["type"], item['genre'] if 'genre' in item else ''))
      self.conn.commit()


  def get_items(self, workflow: Workflow, type='movies', sql='', limit=1000, order_by='id DESC'):
    exe_sql = ' select id, filename, size, title, url, type, score, genre, poster, marked, title_acurate, trained_flag from movies where 1=1 '
    
    if workflow == Workflow.FILTERING:
      exe_sql += " and (title is null or title = '' ) "
    elif workflow == Workflow.TRAINING:
      exe_sql += " and title is not null and title != '' "
    elif workflow == Workflow.QUERYING:
      exe_sql += " and score is not null and score != '' and score != 'unmatched' "
    elif workflow == Workflow.SCORING:
      exe_sql += " and score is null and title is not null and title != '' "
    
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
            "marked": row[9],
            'title_acurate': row[10],
            'trained_flag': row[11]
        })

    return items
    

  def update_item(self, item):
    # id must exist
    if "id" not in item:
        raise ValueError("item must contain 'id'")

    allowed_fields = ["title", "score", "poster", "marked", "genre", "filename", "size", "url", "type", "title_acurate", "trained_flag"]
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


  def del_item(self, item_id):
    self.cur.execute("DELETE FROM movies WHERE id = ?", (item_id,))
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
     

  def del_duplicates(self):
    logger.debug('# Excuting duplicate removal...')
    self.cur.execute("""
    DELETE FROM movies
    WHERE id NOT IN (
        SELECT MIN(id)
        FROM movies
        GROUP BY title
    """)
    self.conn.commit()


      
  def __del__(self):
    self.conn.close()
    
db = MyRargbDB()    
    
if __name__ == "__main__":    
    pass    
