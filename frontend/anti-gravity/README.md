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
