# Creator Bot - Frontend (Anti-Gravity)

A modern, high-energy React interface built with Vite and Vanilla CSS.

## 🎨 Design System
- **Focus**: Premium, symmetrical, and "alive" interface with micro-animations.
- **Glassmorphism**: Subtle backgrounds and gradient borders used for cards and sidebars.
- **Micro-interactions**: Hover effects on all clickable elements to enhance engagement.

## 🧩 Key Components

### 1. PreviewCard (`src/components/PreviewCard.jsx`)
Standardized rendering for:
- **Video Cards**: High-confidence video matches with play overlays.
- **Article Cards**: Knowledge snippets from the creator's site.
- **Channel Fallback Cards**: Links to the official YouTube channel or Topic Search.

### 2. ChatPanel (`src/components/ChatPanel.jsx`)
- Handles conversational state and multi-card response layouts.
- Automatically suppresses text-based source links when graphical cards are present.

### 3. Setup Wizard
- A multi-step onboarding flow for creators to define their identity, approve initial content, and configure their visual style.

## 🛠️ Development

### Setup
```bash
npm install
npm run dev
```

### Environment
Ensure the environment variables point to the correctly running backend (default `localhost:8000`).

### Production Deployments
- Set `VITE_API_BASE_URL` in Vercel to the deployed backend origin. If you omit it, the frontend can build against the wrong host and chat requests will never reach Render.
- Keep backend auth compatible with cross-site requests: `COOKIE_SAMESITE=none` and `COOKIE_SECURE=true`.
- Add your deployed frontend origin to backend `CORS_ORIGINS` or `FRONTEND_URL`. Multiple origins can be comma-separated.
- The client now sends the bearer token returned by `/auth/login` as a fallback when the browser drops the cross-site session cookie, so login and chat remain functional in stricter browser privacy modes.
