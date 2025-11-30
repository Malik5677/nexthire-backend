from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# 1. REPLACE <YOUR_PASSWORD_HERE> with the actual password you copied
# 2. Ensure you keep the angle brackets <> out, just put the password string.
uri = "mongodb+srv://jatinmalik34568_db_user:NuOX9MEKOsfzWzLZ@nexthire-cluster.deqwca2.mongodb.net/?retryWrites=true&w=majority&appName=NextHire-Cluster"

# Create a new client and connect to the server
client = MongoClient(uri, server_api=ServerApi('1'))

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
    
    # Optional: Create/Access a specific database for your project
    db = client['nexthire_db']
    print(f"Ready to use database: {db.name}")
    
except Exception as e:
    print(e)
