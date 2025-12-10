CREATE TABLE movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        size TEXT,
        title TEXT,        
        url TEXT,
        type TEXT    
    , score text, genre text, poster text, marked text default '00')