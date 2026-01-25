# Kraken Trading Bot - UI

The frontend dashboard for the Kraken Trading Bot, built with React and Vite.

## Prerequisites

-   **Node.js**: v18 or higher is recommended.
-   **npm**: Installed with Node.js.

## Getting Started

### 1. Installation

Install the dependencies:

```bash
cd ui
npm install
```

### 2. Development

Start the development server with hot-reloading:

```bash
npm run dev
```

The UI will be available at `http://localhost:5173` (by default).

### 3. Environment Variables

You can configure the API connection using environment variables (e.g., in a `.env` file or shell):

| Variable | Description | Default |
| :--- | :--- | :--- |
| `VITE_API_BASE` | Base URL for the backend API. | `/api` |
| `VITE_API_TOKEN` | Bearer token for authentication (if enabled). | `undefined` |

Example `.env` for local development against a backend running on port 8080:

```env
VITE_API_BASE=http://localhost:8080/api
VITE_API_TOKEN=your-secret-token
```

### 4. Building

To build the UI for production:

```bash
npm run build
```

The artifacts will be generated in the `dist/` directory, which the backend serves in production.
