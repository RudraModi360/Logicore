import os, tempfile, shutil, pandas as pd, sqlite3

def setup_temp_dir(src_dir):
    temp_dir = tempfile.mkdtemp()
    for fname in os.listdir(src_dir):
        if fname.endswith('.csv'):
            shutil.copy(os.path.join(src_dir, fname), temp_dir)
    # copy Drug_clean.csv
    shutil.copy('C:\\Users\\rudra\\Downloads\\Drug_clean.csv', temp_dir)
    return temp_dir

def load_csv_to_sqlite(db_path, csv_dir):
    conn = sqlite3.connect(db_path)
    for csv_file in os.listdir(csv_dir):
        if csv_file.endswith('.csv'):
            df = pd.read_csv(os.path.join(csv_dir, csv_file))
            table = csv_file.replace('.csv','').lower()
            df.to_sql(table, conn, if_exists='replace', index=False)
    conn.commit()
    return conn

def run_query(conn, query):
    return pd.read_sql_query(query, conn)

# Example queries (replace with actual queries 1-15)
queries = {
    1: "-- Q1 query placeholder",
    2: "-- Q2 query placeholder",
    3: "-- Q3 query placeholder",
    4: "-- Q4 query placeholder",
    5: "-- Q5 query placeholder",
    6: "-- Q6 query placeholder",
    # ... up to 15
}

if __name__=='__main__':
    src = r'C:\Users\rudra\Downloads\drive-download-20260704T122821Z-3-001'
    temp_dir = setup_temp_dir(src)
    print('Temp dir created:', temp_dir)
    db_path = os.path.join(temp_dir, 'local.db')
    conn = load_csv_to_sqlite(db_path, temp_dir)
    print('Database loaded.')
    for idx, q in queries.items():
        print(f'Running query {idx}')
        try:
            df = run_query(conn, q)
            print(df.head())
        except Exception as e:
            print('Error:', e)
    conn.close()
    shutil.rmtree(temp_dir)
    print('Temp dir removed.')
