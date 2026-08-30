import os
import requests

APPLICATION_ID = os.environ.get("APPLICATION_ID")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"

commands = {
    "name": "fbembbed",
    "description": "Embed a Facebook post in Discord",
    "options": [
        {
            'name': 'url',
            'description': 'The URL of the Facebook post to embed',
            'type': 3,  # String
            'required': True
        }
    ]
}

response = requests.post(url, json=commands, headers={"Authorization": f"Bot {BOT_TOKEN}"})
print(response.status_code, response.json())