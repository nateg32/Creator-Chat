# Creator Bot Builder

Creator Bot is a high-performance system for building AI-driven chatbots that perfectly mimic the voice, knowledge, and persona of specific content creators. By scraping and ingesting content from social platforms, the bot build a unique knowledge base and adopts the creator’s specific conversational style using a custom Grounded-RAG algorithm.

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.10+**
- **Node.js 18+**
- **PostgreSQL 12+** with `pgvector` extension installed (`CREATE EXTENSION vector;`)

### 2. Environment Setup
Create a `.env` file in the root directory:
```env
OPENAI_API_KEY=your_key
APIFY_TOKEN=your_token
SERPAPI_API_KEY=your_serpapi_key
DATABASE_URL=postgresql://user:pass@localhost:5432/creator_bot
DB_PASSWORD=your_db_password
```

### 3. Run the Backend
```powershell
cd backend
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### 4. Run the Frontend
```powershell
cd frontend/anti-gravity
npm install
npm run dev
```

## 🏗️ Technical Architecture

### Backend (FastAPI)
- **Scraper Router**: Directs requests to platform-specific Apify actors (LinkedIn, Instagram, TikTok).
- **Ingestion Pipeline**: Automated chunking (800 chars), metadata extraction, and embedding generation using `text-embedding-3-small`.
- **Grounded-RAG**: A custom multi-step retrieval loop that re-ranks candidates and enforces the creator's persona while ensuring factual grounding.
- **Creator Video Recommender**: Uses a three-tier confidence model (strong/moderate/weak) so moderate matches return 2–3 creator-owned video cards before falling back to channel search.

### Frontend (React + Vite)
- **Setup Wizard**: Handles creator onboarding and verification of official links (YouTube, Website).
- **Resource Cards**: Premium UI components for rendering Videos, Articles, and "Channel Fallback" cards.
- **Approval Gate**: Staging area for reviewed and approved scraped content.

### Database (Postgres + pgvector)
- **`creators`**: Stores canonical identity metadata (YouTube handles, official domains, course URLs) for verification.
- **`documents` / `chunks`**: Approved knowledge snippets with semantic embeddings.

## 🛠️ In-Depth Troubleshooting & Fixes

- **LinkedIn Scraper Output**: The system is tuned to use the `username` field for LinkedIn actors. Always provide a handle or a full `/in/` URL.
- **OpenAI/Httpx Compatibility**: If you see `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`, ensure `openai >= 1.40.0`.
- **JSON Serialization**: Database objects (datetimes, UUIDs) are automatically handled via `default=str` in all internal serialization calls.
- **Port 8000**: The backend is configured to run on port 8000.

## 📂 Project Structure
- `backend/`: Core logic, API endpoints, and database handlers.
- `frontend/anti-gravity/`: React application and design system.
- `TASKS_COMPLETED.md`: Detailed changelog of recent fixes and implementations.

## 📄 License
Internal Development.
