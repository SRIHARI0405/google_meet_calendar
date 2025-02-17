import os
import json
import datetime
from dotenv import load_dotenv
from openai import OpenAI
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dateutil.parser import parse

load_dotenv()


OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']
TIMEZONE = 'UTC'

class GoogleCalendarClient:
    def __init__(self):
        self.creds = None
        self.service = self.authenticate()

    def authenticate(self):
        if os.path.exists('token.json'):
            self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    GOOGLE_CREDENTIALS_FILE, SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            with open('token.json', 'w') as token:
                token.write(self.creds.to_json())
        
        return build('calendar', 'v3', credentials=self.creds)

    def create_event(self, summary, start_time, end_time, attendees=None, description=None):
        event = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': TIMEZONE,
            },
            'end': {
                'dateTime': end_time,
                'timeZone': TIMEZONE,
            },
            'attendees': [{'email': email} for email in attendees] if attendees else [],
        }

        return self.service.events().insert(
            calendarId='primary',
            body=event,
            sendUpdates='all' if attendees else 'none'
        ).execute()

class OpenAIAssistant:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.assistant = self.create_assistant()
        self.thread = self.client.beta.threads.create()
        self.calendar = GoogleCalendarClient()

    def create_assistant(self):
        return self.client.beta.assistants.create(
            name="Calendar Assistant",
            instructions="You are a calendar scheduling assistant. Use the provided functions to schedule meetings.",
            model="gpt-4-turbo",
            tools=[{
                "type": "function",
                "function": {
                    "name": "create_google_calendar_event",
                    "description": "Create a new Google Calendar event",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "Title of the event"
                            },
                            "start_time": {
                                "type": "string",
                                "format": "date-time",
                                "description": "Start time in ISO 8601 format"
                            },
                            "end_time": {
                                "type": "string",
                                "format": "date-time",
                                "description": "End time in ISO 8601 format"
                            },
                            "attendees": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "format": "email"
                                },
                                "description": "List of attendee email addresses"
                            },
                            "description": {
                                "type": "string",
                                "description": "Detailed description of the event"
                            }
                        },
                        "required": ["summary", "start_time", "end_time"]
                    }
                }
            }]
        )

    def process_function_call(self, tool_call):
        function_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)

        if function_name == "create_google_calendar_event":
            try:
                start_time = parse(arguments["start_time"]).isoformat()
                end_time = parse(arguments["end_time"]).isoformat()
                
                event = self.calendar.create_event(
                    summary=arguments["summary"],
                    start_time=start_time,
                    end_time=end_time,
                    attendees=arguments.get("attendees"),
                    description=arguments.get("description")
                )
                return f"Event created: {event.get('htmlLink')}"
            except Exception as e:
                return f"Error creating event: {str(e)}"
        else:
            return "Unknown function called"

    def run_assistant(self, user_input):
        self.client.beta.threads.messages.create(
            thread_id=self.thread.id,
            role="user",
            content=user_input
        )

        run = self.client.beta.threads.runs.create(
            thread_id=self.thread.id,
            assistant_id=self.assistant.id
        )

        while True:
            run_status = self.client.beta.threads.runs.retrieve(
                thread_id=self.thread.id,
                run_id=run.id
            )
            
            if run_status.status == 'completed':
                break
            elif run_status.status == 'requires_action':
                tool_outputs = []
                for tool_call in run_status.required_action.submit_tool_outputs.tool_calls:
                    output = self.process_function_call(tool_call)
                    tool_outputs.append({
                        "tool_call_id": tool_call.id,
                        "output": output
                    })

                self.client.beta.threads.runs.submit_tool_outputs(
                    thread_id=self.thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )
            elif run_status.status in ['failed', 'cancelled', 'expired']:
                return "Run failed"

        messages = self.client.beta.threads.messages.list(
            thread_id=self.thread.id
        )

        return messages.data[0].content[0].text.value

if __name__ == "__main__":
    assistant = OpenAIAssistant()

    user_query = "Schedule a team meeting next Monday at 2 PM for 1 hour with alice@example.com and bob@example.com"
    response = assistant.run_assistant(user_query)
    print("Assistant Response:", response)