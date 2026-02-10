# AI-Powered Property Management Communication System

A unified intelligent communication hub for managing Airbnb properties, coordinating with cleaners, and handling guest requests across multiple channels (WhatsApp, Airbnb messages).

## Features

- **Automatic Job Assignment**: Ranks cleaners by property familiarity, availability, response rate, and rating
- **AI-Powered Message Interpretation**: Uses Claude to understand informal responses like "ok", "can't", "only 1 and 3"
- **Batch Evening Delivery**: Groups non-urgent jobs and sends one message per cleaner at 6 PM
- **Progressive Reminders**: Automated follow-ups for non-responders
- **Cascading Assignment**: Automatically offers jobs to next-best cleaner on rejection/expiration
- **Guest Issue Coordination**: Handles missing items, early check-in requests, maintenance issues

## Tech Stack

- **Backend**: Python 3.11, FastAPI
- **Database**: SQLite (dev) / PostgreSQL (production)
- **AI**: Anthropic Claude API
- **Messaging**: WhatsApp (Green API), Airbnb API
- **Scheduling**: APScheduler
- **Deployment**: Docker, Railway/Render

## Quick Start

### Local Development

1. Clone the repository and navigate to the python directory:
   ```bash
   cd python
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

5. Run the application:
   ```bash
   uvicorn app.main:app --reload
   ```

6. Access the API documentation at `http://localhost:8000/docs`

### Docker

```bash
docker-compose up --build
```

## API Endpoints

### Webhooks
- `POST /webhook/whatsapp` - WhatsApp message webhook (Green API)
- `POST /webhook/airbnb` - Airbnb message webhook

### Admin Dashboard
- `GET /admin/dashboard` - Dashboard overview statistics
- `POST /admin/jobs/{id}/assign` - Manual job assignment
- `GET /admin/jobs/{id}/rank-cleaners` - Get ranked cleaners for a job
- `POST /admin/batch/run` - Trigger batch delivery
- `POST /admin/reminders/run` - Trigger reminder check

### Resources
- `GET/POST /properties` - Property management
- `GET/POST /cleaners` - Cleaner management
- `GET/POST /jobs` - Job management
- `GET/POST /guests` - Guest management

## Configuration

Key environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | Required |
| `GREEN_API_INSTANCE_ID` | WhatsApp instance ID | Required |
| `GREEN_API_TOKEN` | WhatsApp API token | Required |
| `DATABASE_URL` | Database connection string | SQLite |
| `BATCH_DELIVERY_HOUR` | Hour for batch delivery | 18 (6 PM) |
| `DEFAULT_RESPONSE_TIMEOUT_HOURS` | Offer expiration time | 24 |

## Message Interpretation Examples

The AI understands various informal responses:

| Message | Interpretation |
|---------|---------------|
| "ok", "yes", "sure" | Accept all pending offers |
| "can't", "no", "not available" | Reject all offers |
| "only 1 and 3" | Accept jobs 1 & 3, reject others |
| "what time?" | Question about schedule |
| "on my way", "done" | Status update |

## Cleaner Ranking Algorithm

Cleaners are ranked using weighted scores:
- Property Familiarity: 40%
- Availability Score: 30%
- Response Rate: 20%
- Rating: 10%

## Deployment

### Railway

1. Connect your GitHub repository
2. Set environment variables in Railway dashboard
3. Deploy using the `railway.json` configuration

### Render

1. Create a new Web Service from your repository
2. Use the `render.yaml` Blueprint
3. Configure environment variables

## License

MIT
