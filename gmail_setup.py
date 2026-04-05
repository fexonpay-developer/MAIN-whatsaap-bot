from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import os

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    """Authenticate and get Gmail credentials"""
    creds = None
    
    # Check if token.pickle exists
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            
            # Don't open browser automatically
            flow.redirect_uri = 'http://localhost:8080/'
            auth_url, _ = flow.authorization_url(prompt='consent')
            
            print('\n' + '='*70)
            print('COPY THIS URL AND OPEN IT IN YOUR BROWSER:')
            print('='*70)
            print(auth_url)
            print('='*70)
            print('\nAfter authorizing, you will be redirected to a URL.')
            print('Copy the ENTIRE URL from your browser and paste it here:')
            print()
            
            code_url = input('Paste the full redirect URL here: ').strip()
            
            # Extract the code from the URL
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(code_url)
            code = parse_qs(parsed.query).get('code', [None])[0]
            
            if not code:
                print('ERROR: Could not extract authorization code from URL')
                return None
            
            flow.fetch_token(code=code)
            creds = flow.credentials
        
        # Save credentials for next time
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return creds

def setup_gmail_watch(creds):
    """Set up Gmail push notifications"""
    try:
        service = build('gmail', 'v1', credentials=creds)
        
        # Get the label ID for "Fexonpay Invoices"
        labels = service.users().labels().list(userId='me').execute()
        label_id = None
        
        for label in labels.get('labels', []):
            if label['name'] == 'Fexonpay Invoices':
                label_id = label['id']
                break
        
        if not label_id:
            print('ERROR: Label "Fexonpay Invoices" not found!')
            print('Please create the label in Gmail first.')
            return
        
        print(f'Found label: Fexonpay Invoices (ID: {label_id})')
        
        # Set up watch
        watch_request = {
            'labelIds': [label_id],
            'topicName': 'projects/fexonpay-production/topics/gmail-invoices'
        }
        
        response = service.users().watch(
            userId='me',
            body=watch_request
        ).execute()
        
        print('\n' + '='*70)
        print('SUCCESS! Gmail watch set up successfully!')
        print('='*70)
        print(f'History ID: {response.get("historyId")}')
        print(f'Expiration: {response.get("expiration")}')
        print('\nGmail will now send push notifications to:')
        print('https://api.fexonpay.com/webhooks/gmail')
        print('='*70)
        
    except Exception as e:
        print(f'Error setting up Gmail watch: {e}')

if __name__ == '__main__':
    print('Authenticating with Gmail...')
    creds = authenticate_gmail()
    if creds:
        print('Authentication successful!')
        print('\nSetting up Gmail push notifications...')
        setup_gmail_watch(creds)
