import os.path
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def main():
    if not os.path.exists('credentials.json'):
        print("\n❌ Error: credentials.json not found in /home/fexonpay/")
        return

    # Use the Desktop flow with an explicit redirect URI
    flow = InstalledAppFlow.from_client_secrets_file(
        'credentials.json',
        scopes=SCOPES,
        redirect_uri='http://localhost'
    )
    
    auth_url, _ = flow.authorization_url(prompt='consent')
    
    print("\n1. Visit this URL in your browser:")
    print(auth_url)
    
    print("\n2. After authorizing, the browser will redirect you to http://localhost/.")
    print("Copy the long code from the URL bar (the part after ?code=).")
    
    code = input("\n3. Paste the 'code' here: ").strip()
    
    flow.fetch_token(code=code)
    
    with open('token.json', 'w') as token:
        token.write(flow.credentials.to_json())
    
    print("\n✅ Success! token.json has been generated.")

if __name__ == '__main__':
    main()
