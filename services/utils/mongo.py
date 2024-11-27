from pymongo import MongoClient
from django.conf import settings

def get_mongo_client():
    return MongoClient("mongodb+srv://nitinubale:developerapi@cluster0.3pgnu.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")

def get_mongo_db():
    client = get_mongo_client()
    return client["Fastlane"]
