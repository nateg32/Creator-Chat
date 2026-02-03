# Environment Variables Setup

## Quick Setup (Recommended)

1. **Create a `.env` file in the `backend/` directory:**

```powershell
cd "C:\Users\Nathan\Documents\Creator Bot\backend"
```

2. **Copy the example file and edit it:**

```powershell
Copy-Item env.example .env
notepad .env
```

3. **Fill in your actual values in `.env`:**

```
APIFY_TOKEN=your_actual_apify_token_here
OPENAI_API_KEY=your_actual_openai_key_here
DB_PASSWORD=Kipkogey2019!
TRANSCRIBE_ON_INGEST=false
```

4. **Save and close the file**

5. **Restart your backend server** - it will automatically load the `.env` file

## Manual Setup (Terminal Variables)

If you prefer to set variables in the terminal:

1. **Cancel any continuation prompts** (press `Ctrl+C` until you see a normal prompt)

2. **Set variables one at a time:**

```powershell
$env:APIFY_TOKEN = "your_apify_token_here"
$env:OPENAI_API_KEY = "your_openai_key_here"
$env:DB_PASSWORD = "Kipkogey2019!"
$env:TRANSCRIBE_ON_INGEST = "false"
```

3. **Verify they're set:**

```powershell
echo $env:APIFY_TOKEN
```

4. **Start backend from the SAME terminal:**

```powershell
cd "C:\Users\Nathan\Documents\Creator Bot"
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

## Important Notes

- **`.env` file is recommended** - it persists across terminal sessions
- **Never commit `.env` to git** - it contains secrets
- **Rotate your Apify token** if you've shared it publicly
- **Backend must be restarted** after changing environment variables
