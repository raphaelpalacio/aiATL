from tokenize import group
from flask import Flask, render_template
import requests
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

from pymongo import MongoClient
from pymongo.server_api import ServerApi


app = Flask(__name__)

load_dotenv()
GM_API_KEY = os.getenv('GM_API_KEY')
ONEAI_KEY = os.getenv('ONEAI_KEY')

# database configuration--------------------------------------------------------
MONGO_USERNAME = os.getenv('MONGO_USERNAME')
MONGO_PW = os.getenv('MONGO_PW')
uri = f"mongodb+srv://{MONGO_USERNAME}:{MONGO_PW}@freecluster.xfcsiur.mongodb.net/?retryWrites=true&w=majority"
client = MongoClient(uri, server_api=ServerApi('1'))

db = client['AIATL']
groups_collection = db['Groups']
messages_collection = db['Messages']
oneai_collection = db['OneAISummary']


def insert_into_mongodb(groups, collection):
    for group in groups:
        collection.update_one({'id': group['id']}, {'$set': group}, upsert=True)


def retrieve_from_mongodb(collection, group_id, before_time):
    query = {
        'group_id': group_id,
    }
    if before_time != None:
        query['created_at'] = {'$gt': before_time}

    groups_cursor = collection.find(query)
    return list(groups_cursor)
    

# fetching the group_ids -------------------------------------------------------
def fetchGroupData(access_token):
    url = 'https://api.groupme.com/v3/groups'
    headers = {
        'Content-Type': 'application/json',
        'X-Access-Token': access_token,
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to get groups. Status code: {response.status_code}")
        return None


@app.route("/home")
def fetch_group_data():
    data = fetchGroupData(access_token=GM_API_KEY)
    groups = []
    for group in data['response']:
        group_info = {'id': group['id'], 
                      'name': group['name'],
                      'image_url': group['image_url']}
        groups.append(group_info)

    insert_into_mongodb(groups, groups_collection)
    groups_from_db = retrieve_from_mongodb(groups_collection, None, None)
    for group in groups_from_db:
        print(group)
    return render_template('home.html', group_data=groups_from_db)


# fetching the group_messages---------------------------------------------------
def getMessages(access_token, group_id):
    url = f"https://api.groupme.com/v3/groups/{group_id}/messages"
    
    # Change for different time periods
    one_week_ago = datetime.now() - timedelta(weeks=1)
    one_day_ago = datetime.now() - timedelta(days=1)

    params = {
        'token': access_token,
        'limit': 100
    }

    all_messages = []
    fetch_more = True

    while fetch_more:
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            data = response.json()['response']
            messages = data['messages']

            if messages:
                last_message_time = datetime.utcfromtimestamp(messages[-1]['created_at'])
                fetch_more = last_message_time > one_week_ago
                params['before_id'] = messages[-1]['id']
                
                for message in messages:
                    message_time = datetime.utcfromtimestamp(message['created_at'])
                    # if message_time > one_day_ago:
                    if message_time > one_week_ago:
                        message_info = {
                            'id': message['id'],
                            'group_id': message['group_id'],
                            'name': message['name'],
                            'text': message['text'],
                            'created_at': datetime.utcfromtimestamp(message['created_at'])
                        }
                        all_messages.append(message_info)
                    else:
                        fetch_more = False
                        break
        elif response.status_code == 304:
            print('No more messages found')
            break
        else:
            print("Error:", response.status_code, response.text)
            fetch_more = False
    
    return all_messages
        

def insert_messages_into_mongodb(messages, collection):
    for message in messages:
        collection.update_one({'id': message['id']}, {'$set': message}, upsert=True)


def retrieve_messages_from_mongodb(collection, filter):
    messages_cursor = collection.find({'group_id': f'{filter}'}) # Filter must be string
    return list(messages_cursor)


def format_messages(messages_from_db):
    formatted_messages = []
    for message in messages_from_db:
        try:
            speaker = message.get("name","")
        except KeyError:
            speaker = "Unknown"
        utterance = message.get("text", "")
        formatted_message = {"speaker": speaker, "utterance": utterance}
        formatted_messages.append(formatted_message)

    return formatted_messages

# TODO: Post request not going through
def oneAi_summary(oneAi_token, messages, skill): # message is in {name: message} form
    print('1')
    url = "https://api.oneai.com/api/v0/pipeline"
  
    headers = {
        "api-key": oneAi_token, 
        "content-type": "application/json"
    }
    payload = {
        "input": messages,
        "input_type": "conversation",
        "content_type": "application/json",
        "output_type": "json",
        "multilingual": {
            "enabled": True
        },
        "steps": [
            {
                "skill": skill
            }
        ],
    }
    # r = requests.post(url, json=payload, headers=headers)
    # data = r.json()
    # print(data)
    print('2')
    try:
        print('3')
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        print(data)
        return data['output'][0]['contents'][0]['utterance']  # text
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Error during requests to {url}: {e}")
    except KeyError as e:
        print(f"Key error in parsing response: {e}")
    except IndexError as e:
        print(f"Index error in parsing response: {e}")
    return None


@app.route("/group/<int:group_id>")
def load_group_page(group_id):
    messages = getMessages(access_token=GM_API_KEY, group_id=group_id)
    insert_into_mongodb(messages, messages_collection)
    one_week_ago = datetime.now() - timedelta(weeks=1)
    one_day_ago = datetime.now() - timedelta(days=1)
    messages_day = retrieve_from_mongodb(messages_collection, group_id=str(group_id), before_time=one_day_ago)
    messages_week = retrieve_from_mongodb(messages_collection, group_id=str(group_id), before_time=one_week_ago)
    messages_day_formatted = format_messages(messages_day)
    messages_week_formatted = format_messages(messages_week)
    print(messages_day_formatted)
    print(messages_week_formatted)
    print("A")
    summary_day = oneAi_summary(ONEAI_KEY, messages_day_formatted, "summarize")
    summary_week = oneAi_summary(ONEAI_KEY, messages_week_formatted, "summarize")
    print(f'Summary: {summary_day}\n')
    print(f'Summary: {summary_week}\n')
    print("B")
    # action_items_week = oneAi_summary(ONEAI_KEY, messages_week_formatted, "action-items")
    # print(f'Action Items Week: {action_items_week}\n')
    action_items = "Lunch at Chick-fil-A on Saturday at 8 pm."
    print("C")

    return render_template('group_page.html', daily_summary=summary_day, weekly_summary=summary_week, action_items=action_items)
# -------------------------------------------------------------------


@app.route("/")
def index():
    return render_template('index.html')


if __name__ == "__main__":
    app.run(debug=True)
